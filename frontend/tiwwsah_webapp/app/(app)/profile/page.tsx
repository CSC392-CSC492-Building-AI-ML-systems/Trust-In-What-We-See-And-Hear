import { PagePlaceholder } from "@/components/page-placeholder";
import { UserRound } from "lucide-react";

export default function ProfilePage() {
  return (
      <section className="page">
        <div className="profile_card">
            <div className="p-50 border-9">
                <div className="profile_icon"> <UserRound aria-hidden="true" /> </div>
            </div>
          <div className="profile_info"> hi </div>
        </div>
      </section>

  );
}

