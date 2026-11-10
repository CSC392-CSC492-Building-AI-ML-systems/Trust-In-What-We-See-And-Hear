import logging
from datetime import datetime, timedelta
from typing import Annotated, Any, NoReturn
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status

from app.core import clock
from app.core.auth import AuthenticatedUser, get_current_user
from app.core.config import Settings
from app.core.errors import api_error, error_responses, merge_responses
from app.integrations import voice_pipeline
from app.integrations.supabase import (
    SupabaseConflictError,
    SupabaseDatabase,
    SupabaseForeignKeyError,
    SupabaseStorage,
    SupabaseUnavailableError,
    get_supabase_db,
    get_supabase_storage,
)
from app.modules.voices.audio import SUPPORTED_FORMATS, inspect_audio
from app.modules.voices.schemas import VoiceResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users/me/voice", tags=["Voice"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
SupabaseDb = Annotated[SupabaseDatabase, Depends(get_supabase_db)]
Storage = Annotated[SupabaseStorage, Depends(get_supabase_storage)]

VOICE_COLUMNS = (
    "id,status,error_message,sample_duration_seconds,voice_owner_consent_at,updated_at,"
    "sample_storage_path,model_storage_path"
)
ONE_VOICE_PER_USER_CONSTRAINT = "voices_user_id_key"
# Far longer than an upload request normally takes, so a file this old with no
# record pointing to it is a leftover. Not a hard guarantee: a request that
# keeps making slow progress could take longer.
LEFTOVER_GRACE = timedelta(minutes=10)

NOT_FOUND = error_responses(
    status.HTTP_404_NOT_FOUND, ("voice_not_found", "The current user has no voice yet.")
)


@router.put(
    "",
    response_model=VoiceResponse,
    responses=merge_responses(
        error_responses(
            status.HTTP_400_BAD_REQUEST,
            ("owner_consent_required", "`owner_consent` was not `true`."),
            ("audio_too_short", "The recording is shorter than the minimum length."),
            ("audio_too_long", "The recording is longer than the maximum length."),
        ),
        error_responses(
            status.HTTP_409_CONFLICT,
            ("profile_required", "The current user has not finished onboarding."),
            ("voice_changed", "Another upload changed the voice at the same time; retry."),
        ),
        error_responses(
            status.HTTP_413_CONTENT_TOO_LARGE,
            ("file_too_large", "The file is larger than the maximum size."),
        ),
        error_responses(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            ("unsupported_audio", f"The file is not {SUPPORTED_FORMATS} audio."),
        ),
    ),
)
async def upload_voice_sample(
    audio: Annotated[
        UploadFile,
        File(description=f"Reference recording of the user's own voice: {SUPPORTED_FORMATS}."),
    ],
    owner_consent: Annotated[
        bool,
        Form(
            description=(
                "Must be `true`: the user confirms the recording is their own voice "
                "and agrees to it being cloned for simulated calls."
            )
        ),
    ],
    request: Request,
    user: CurrentUser,
    supabase_db: SupabaseDb,
    storage: Storage,
) -> VoiceResponse:
    """Upload the current user's voice reference sample, replacing any previous one.

    The file is checked before anything is stored. A replaced sample, and
    anything derived from it, is deleted, and the voice goes back to `uploaded`.
    """
    settings: Settings = request.app.state.settings
    if not owner_consent:
        api_error(
            status.HTTP_400_BAD_REQUEST,
            "owner_consent_required",
            "Confirm that this is your own voice and that you agree to it being cloned.",
        )
    data = await audio.read(settings.voice_sample_max_bytes + 1)
    if len(data) > settings.voice_sample_max_bytes:
        api_error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "file_too_large",
            f"The file must be at most {settings.voice_sample_max_bytes} bytes.",
        )
    info = inspect_audio(data)
    if info is None:
        api_error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "unsupported_audio",
            f"Upload {SUPPORTED_FORMATS} audio.",
        )
    if info.duration_seconds < settings.voice_sample_min_seconds:
        api_error(
            status.HTTP_400_BAD_REQUEST,
            "audio_too_short",
            f"The recording must be at least {settings.voice_sample_min_seconds:g} seconds long.",
        )
    if info.duration_seconds > settings.voice_sample_max_seconds:
        api_error(
            status.HTTP_400_BAD_REQUEST,
            "audio_too_long",
            f"The recording must be at most {settings.voice_sample_max_seconds:g} seconds long.",
        )

    existing = await _find_voice(supabase_db, user.user_id)
    # A new object name per upload, so a failed upload never overwrites the
    # sample the database still points to.
    sample_path = f"{user.user_id}/{uuid4()}.{info.extension}"
    await storage.upload(settings.voice_samples_bucket, sample_path, data, info.content_type)

    values = {
        "sample_storage_path": sample_path,
        "sample_duration_seconds": round(info.duration_seconds, 2),
        "model_storage_path": None,
        "status": "uploaded",
        "error_message": None,
        "voice_owner_consent_at": clock.to_iso(clock.now()),
    }
    try:
        if existing is None:
            saved = await _insert_voice(supabase_db, user.user_id, values)
        else:
            saved = await _replace_voice(supabase_db, user.user_id, existing, values)
    except SupabaseUnavailableError:
        # The write may have been saved even though no answer came back, so the
        # new file is kept; the next upload or delete sweeps it if unused.
        raise
    except BaseException:
        await _remove_quietly(storage, settings.voice_samples_bucket, sample_path)
        raise

    try:
        if existing is not None:
            await _remove_voice_files(storage, settings, existing)
        await _sweep_leftovers(storage, settings, user.user_id, keep={sample_path})
    except Exception:
        logger.exception("Could not delete replaced voice files; the next change retries.")
    await voice_pipeline.on_sample_uploaded(UUID(saved["id"]), user.user_id, sample_path)
    return _to_voice(saved)


@router.get("", response_model=VoiceResponse, responses=NOT_FOUND)
async def get_voice(user: CurrentUser, supabase_db: SupabaseDb) -> VoiceResponse:
    """Return the status of the current user's voice."""
    voice = await _find_voice(supabase_db, user.user_id)
    if voice is None:
        _voice_not_found()
    return _to_voice(voice)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(
        status.HTTP_409_CONFLICT,
        ("voice_changed", "An upload changed the voice at the same time; retry."),
    ),
)
async def delete_voice(
    request: Request, user: CurrentUser, supabase_db: SupabaseDb, storage: Storage
) -> None:
    """Delete the current user's voice: its stored files, then the record.

    Safe to repeat: it succeeds when there is no voice. If deleting files
    fails, it returns `503` and keeps the record, so retrying finishes the
    deletion. It also deletes files failed earlier requests left behind, once
    they are older than a few minutes (newer ones may belong to an upload
    still in progress).
    """
    settings: Settings = request.app.state.settings
    voice = await _find_voice(supabase_db, user.user_id)
    if voice is not None:
        # Files first, while the record still names them: if this fails, a retry
        # finds the same record and deletes them. No other record can point to
        # them, since every upload has its own path.
        await _remove_voice_files(storage, settings, voice)
        path = voice["sample_storage_path"]
        deleted = await supabase_db.delete(
            "voices",
            {
                "id": f"eq.{voice['id']}",
                "user_id": f"eq.{user.user_id}",
                # Only the sample this request read, so a concurrent upload is
                # never left without its record.
                "sample_storage_path": "is.null" if path is None else f"eq.{path}",
            },
            "id",
        )
        if not deleted:
            _voice_changed()

    current = await _find_voice(supabase_db, user.user_id)
    keep = set() if current is None else {
        current["sample_storage_path"], current["model_storage_path"]
    }
    await _sweep_leftovers(storage, settings, user.user_id, keep)

async def _find_voice(supabase_db: SupabaseDatabase, user_id: UUID) -> dict[str, Any] | None:
    rows = await supabase_db.select("voices", {"user_id": f"eq.{user_id}"}, VOICE_COLUMNS)
    return rows[0] if rows else None


async def _insert_voice(
    supabase_db: SupabaseDatabase, user_id: UUID, values: dict[str, Any]
) -> dict[str, Any]:
    try:
        return await supabase_db.insert(
            "voices", {**values, "user_id": str(user_id)}, VOICE_COLUMNS
        )
    except SupabaseForeignKeyError:
        api_error(status.HTTP_409_CONFLICT, "profile_required", "Finish onboarding first.")
    except SupabaseConflictError as exc:
        if exc.constraint == ONE_VOICE_PER_USER_CONSTRAINT:
            _voice_changed()
        raise


async def _replace_voice(
    supabase_db: SupabaseDatabase,
    user_id: UUID,
    existing: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    old_path = existing["sample_storage_path"]
    rows = await supabase_db.update(
        "voices",
        {
            "id": f"eq.{existing['id']}",
            "user_id": f"eq.{user_id}",
            # Only replace the sample this request read, so two uploads at the
            # same time cannot both win and orphan a file.
            "sample_storage_path": "is.null" if old_path is None else f"eq.{old_path}",
        },
        values,
        VOICE_COLUMNS,
    )
    if not rows:
        _voice_changed()
    return rows[0]


async def _remove_quietly(storage: SupabaseStorage, bucket: str, path: str) -> None:
    try:
        await storage.remove(bucket, [path])
    except Exception:
        logger.exception("Could not delete %s from Storage bucket %s.", path, bucket)


async def _remove_voice_files(
    storage: SupabaseStorage, settings: Settings, voice: dict[str, Any]
) -> None:
    for bucket, path in (
        (settings.voice_samples_bucket, voice["sample_storage_path"]),
        (settings.voice_models_bucket, voice["model_storage_path"]),
    ):
        if path:
            await storage.remove(bucket, [path])


async def _sweep_leftovers(
    storage: SupabaseStorage, settings: Settings, user_id: UUID, keep: set[str | None]
) -> None:
    """Delete files in the user's folders that failed requests left behind.

    Only files older than LEFTOVER_GRACE are deleted: a newer one may belong to
    an upload still in progress that has not saved its record yet.
    """
    cutoff = clock.now() - LEFTOVER_GRACE
    for bucket in (settings.voice_samples_bucket, settings.voice_models_bucket):
        objects = await storage.list_folder(bucket, str(user_id))
        stale = [
            item["path"]
            for item in objects
            if item["path"] not in keep
            and item["created_at"] is not None
            and datetime.fromisoformat(item["created_at"]) < cutoff
        ]
        await storage.remove(bucket, stale)

def _to_voice(row: dict[str, Any]) -> VoiceResponse:
    return VoiceResponse(
        status=row["status"],
        error_message=row["error_message"],
        sample_duration_seconds=row["sample_duration_seconds"],
        owner_consent_at=row["voice_owner_consent_at"],
        updated_at=row["updated_at"],
    )


def _voice_not_found() -> NoReturn:
    api_error(status.HTTP_404_NOT_FOUND, "voice_not_found", "You have not uploaded a voice yet.")


def _voice_changed() -> NoReturn:
    api_error(
        status.HTTP_409_CONFLICT,
        "voice_changed",
        "Your voice was changed by another upload at the same time. Try again.",
    )
