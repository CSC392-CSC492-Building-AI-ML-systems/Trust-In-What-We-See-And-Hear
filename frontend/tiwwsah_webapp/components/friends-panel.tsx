"use client";

import { apiFetch } from "@/lib/api-fetch";
import {
  Check,
  CircleCheck,
  Clock3,
  LoaderCircle,
  Search,
  UserMinus,
  UserPlus,
  UsersRound,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";

type PublicUser = {
  user_id: string;
  username: string;
  first_name: string;
  last_name: string;
  verified: boolean;
};

type Friend = PublicUser & { friends_since: string };

type FriendRequest = {
  id: string;
  direction: "incoming" | "outgoing";
  user: PublicUser;
  created_at: string;
};

type Page<T> = { items: T[]; limit: number; offset: number };
type ApiError = { detail?: { code?: string; message?: string } };
type FriendsTab = "friends" | "incoming" | "outgoing";

const API_URL = process.env.NEXT_PUBLIC_API_URL;
const PAGE_SIZE = 100;

async function readResponse<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  const error = await response.json().catch(() => null) as ApiError | null;
  throw new Error(error?.detail?.message ?? `Request failed (${response.status}).`);
}

function displayName(user: PublicUser) {
  return `${user.first_name} ${user.last_name}`.trim();
}

function initials(user: PublicUser) {
  return `${user.first_name[0] ?? ""}${user.last_name[0] ?? ""}`.toUpperCase();
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

async function fetchFriendsData() {
  if (!API_URL) throw new Error("NEXT_PUBLIC_API_URL is not configured.");

  const params = `limit=${PAGE_SIZE}&offset=0`;
  const [friendsResponse, incomingResponse, outgoingResponse] = await Promise.all([
    apiFetch(`${API_URL}/api/v1/friends?${params}`, "GET"),
    apiFetch(`${API_URL}/api/v1/friend-requests?direction=incoming&${params}`, "GET"),
    apiFetch(`${API_URL}/api/v1/friend-requests?direction=outgoing&${params}`, "GET"),
  ]);

  const [friendsPage, incomingPage, outgoingPage] = await Promise.all([
    readResponse<Page<Friend>>(friendsResponse),
    readResponse<Page<FriendRequest>>(incomingResponse),
    readResponse<Page<FriendRequest>>(outgoingResponse),
  ]);

  return {
    friends: friendsPage.items,
    incoming: incomingPage.items,
    outgoing: outgoingPage.items,
  };
}

export function FriendsPanel() {
  const [activeTab, setActiveTab] = useState<FriendsTab>("friends");
  const [friends, setFriends] = useState<Friend[]>([]);
  const [incoming, setIncoming] = useState<FriendRequest[]>([]);
  const [outgoing, setOutgoing] = useState<FriendRequest[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadFriends = useCallback(async () => {
    setError("");
    setIsLoading(true);

    try {
      const data = await fetchFriendsData();
      setFriends(data.friends);
      setIncoming(data.incoming);
      setOutgoing(data.outgoing);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load friends.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;

    void fetchFriendsData()
      .then((data) => {
        if (!active) return;
        setFriends(data.friends);
        setIncoming(data.incoming);
        setOutgoing(data.outgoing);
      })
      .catch((loadError: unknown) => {
        if (active) setError(loadError instanceof Error ? loadError.message : "Unable to load friends.");
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });

    return () => { active = false; };
  }, []);

  async function sendRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!API_URL) return;
    const form = event.currentTarget;
    const username = String(new FormData(form).get("username")).trim();
    if (!username) return;

    setBusyAction("send");
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`${API_URL}/api/v1/friend-requests`, "POST", { username });
      const request = await readResponse<FriendRequest>(response);
      setOutgoing((current) => [request, ...current]);
      setNotice(`Friend request sent to @${request.user.username}.`);
      setActiveTab("outgoing");
      form.reset();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to send request.");
    } finally {
      setBusyAction("");
    }
  }

  async function acceptRequest(request: FriendRequest) {
    if (!API_URL) return;
    setBusyAction(`accept:${request.id}`);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`${API_URL}/api/v1/friend-requests/${request.id}/accept`, "POST");
      const friend = await readResponse<Friend>(response);
      setIncoming((current) => current.filter((item) => item.id !== request.id));
      setFriends((current) => [friend, ...current]);
      setNotice(`${displayName(friend)} is now your friend.`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to accept request.");
    } finally {
      setBusyAction("");
    }
  }

  async function declineRequest(request: FriendRequest) {
    if (!API_URL) return;
    setBusyAction(`decline:${request.id}`);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`${API_URL}/api/v1/friend-requests/${request.id}/decline`, "POST");
      if (!response.ok) await readResponse<never>(response);
      setIncoming((current) => current.filter((item) => item.id !== request.id));
      setNotice(`Request from @${request.user.username} declined.`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to decline request.");
    } finally {
      setBusyAction("");
    }
  }

  async function removeFriend(friend: Friend) {
    if (!API_URL || !window.confirm(`Remove ${displayName(friend)} from your friends?`)) return;
    setBusyAction(`remove:${friend.user_id}`);
    setError("");
    setNotice("");
    try {
      const response = await apiFetch(`${API_URL}/api/v1/friends/${friend.user_id}`, "DELETE");
      if (!response.ok) await readResponse<never>(response);
      setFriends((current) => current.filter((item) => item.user_id !== friend.user_id));
      setNotice(`${displayName(friend)} was removed from your friends.`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to remove friend.");
    } finally {
      setBusyAction("");
    }
  }

  const tabs: Array<{ id: FriendsTab; label: string; count: number }> = [
    { id: "friends", label: "Friends", count: friends.length },
    { id: "incoming", label: "Incoming", count: incoming.length },
    { id: "outgoing", label: "Sent", count: outgoing.length },
  ];

  return (
    <section className="page friends-page">
      <header className="page-header friends-header">
        <div>
          <p className="eyebrow">Trusted contacts</p>
          <h1>Friends</h1>
          <p>Connect with people you trust before requesting permission for voice-based simulations.</p>
        </div>
      </header>

      <div className="friends-list-card">
        <div className="friends-add-section">
          <div><h2>Add a friend</h2><p>Search using their exact username. They will need to accept your request.</p></div>
          <form className="friends-search" onSubmit={sendRequest}>
            <label htmlFor="friend-username">Username</label>
            <div><Search aria-hidden="true" /><input id="friend-username" name="username" type="text" placeholder="e.g. alexmorgan" autoComplete="off" required /></div>
            <button type="submit" disabled={busyAction === "send"}>
              {busyAction === "send" ? <LoaderCircle className="spin" aria-hidden="true" /> : <UserPlus aria-hidden="true" />}
              {busyAction === "send" ? "Sending…" : "Send request"}
            </button>
          </form>
        </div>

          <div className="friends-tabs" role="tablist" aria-label="Friend lists">
            {tabs.map((tab) => (
              <button key={tab.id} type="button" role="tab" aria-selected={activeTab === tab.id} className={activeTab === tab.id ? "friends-tab-active" : ""} onClick={() => setActiveTab(tab.id)}>
                {tab.label}<span>{tab.count}</span>
              </button>
            ))}
          </div>

          {error && <div className="friends-alert friends-alert-error" role="alert"><X aria-hidden="true" /><p>{error}</p><button type="button" onClick={() => void loadFriends()}>Try again</button></div>}
          {notice && <div className="friends-alert friends-alert-success" role="status"><CircleCheck aria-hidden="true" /><p>{notice}</p></div>}

          {isLoading ? (
            <div className="friends-empty"><LoaderCircle className="spin" aria-hidden="true" /><h2>Loading your connections…</h2></div>
          ) : activeTab === "friends" ? (
            friends.length ? <div className="friends-rows">{friends.map((friend) => (
              <article className="friend-row" key={friend.user_id}>
                <div className="friend-avatar">{initials(friend)}</div>
                <UserIdentity user={friend} detail={`Friends since ${formatDate(friend.friends_since)}`} />
                <button className="friend-secondary-action" type="button" onClick={() => void removeFriend(friend)} disabled={busyAction === `remove:${friend.user_id}`}><UserMinus aria-hidden="true" />{busyAction === `remove:${friend.user_id}` ? "Removing…" : "Remove"}</button>
              </article>
            ))}</div> : <EmptyList icon={UsersRound} title="No friends yet" description="Send a request by username to start building your trusted circle." />
          ) : activeTab === "incoming" ? (
            incoming.length ? <div className="friends-rows">{incoming.map((request) => (
              <article className="friend-row" key={request.id}>
                <div className="friend-avatar">{initials(request.user)}</div>
                <UserIdentity user={request.user} detail={`Requested ${formatDate(request.created_at)}`} />
                <div className="friend-actions">
                  <button className="friend-primary-action" type="button" onClick={() => void acceptRequest(request)} disabled={busyAction !== ""}><Check aria-hidden="true" />{busyAction === `accept:${request.id}` ? "Accepting…" : "Accept"}</button>
                  <button className="friend-secondary-action" type="button" onClick={() => void declineRequest(request)} disabled={busyAction !== ""}><X aria-hidden="true" />{busyAction === `decline:${request.id}` ? "Declining…" : "Decline"}</button>
                </div>
              </article>
            ))}</div> : <EmptyList icon={Clock3} title="No incoming requests" description="New requests from other users will appear here." />
          ) : outgoing.length ? <div className="friends-rows">{outgoing.map((request) => (
            <article className="friend-row" key={request.id}>
              <div className="friend-avatar">{initials(request.user)}</div>
              <UserIdentity user={request.user} detail={`Sent ${formatDate(request.created_at)}`} />
              <span className="pending-badge"><Clock3 aria-hidden="true" />Pending</span>
            </article>
          ))}</div> : <EmptyList icon={UserPlus} title="No sent requests" description="Requests you send will stay here until they are accepted or declined." />}
      </div>
    </section>
  );
}

function UserIdentity({ user, detail }: { user: PublicUser; detail: string }) {
  return <div className="friend-identity"><div><h2>{displayName(user)}</h2></div><p>@{user.username}</p><small>{detail}</small></div>;
}

function EmptyList({ icon: Icon, title, description }: { icon: typeof UsersRound; title: string; description: string }) {
  return <div className="friends-empty"><span><Icon aria-hidden="true" /></span><h2>{title}</h2><p>{description}</p></div>;
}
