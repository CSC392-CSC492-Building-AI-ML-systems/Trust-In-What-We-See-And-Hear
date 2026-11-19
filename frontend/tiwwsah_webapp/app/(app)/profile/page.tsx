"use client";
import { PagePlaceholder } from "@/components/page-placeholder";
import { UserRound } from "lucide-react";
import {useState, useEffect} from "react";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";
import {apiFetch} from "../../../lib/api-fetch";

export default function ProfilePage() {
    const [username, setUserName] = useState();
    const [verified, isVerified] = useState(false);
    const [firstName, setFirstName] = useState();
    const [lastName, setLastName] = useState();
    const[loaded, isLoaded] = useState(false);
    const[editing, isEditing] = useState(false);
    const empty = "Loading...";

    async function loadUser() {
        try{
            isLoaded(false);
            const response = await apiFetch(`${process.env.NEXT_PUBLIC_API_URL}/api/v1/users/me`,"GET")
            if (!response.ok) {
                throw new Error("Unable to load profile");
            }
            const data = await response.json()
            setUserName(data.username);
            isVerified(data.verified);
            setFirstName(data.first_name);
            setLastName(data.last_name);
            isLoaded(true);
        }
        catch(ex){
            console.error(ex);
        }
    }

    function checkStatus() {
        if (verified) {return (<span>Verified</span>);}
        else {return(<span>Unverified</span>);}
    }

    useEffect(() => { loadUser(); }, []);
    //useEffect(() => {  }, [loaded]);

    function mainPage(){
        if (!editing){ return (information()); }
        else { return (updateProfile()); }
    }

    // ASK ADRIEN ABOUT EDITING THE PF
    function updateProfile() {
        return (
            <div className="info_container">
                <div className="profile">
                    <div className="profile_icon"><UserRound aria-hidden="true"/></div>
                    <button className="update_button"> Save Edits </button>
                </div>
                <div className="profile_info">
                    <b>Username:</b>
                    <br/>
                    <div className="profile_text">{username}</div>
                    <b>First Name:</b>
                    <br/>
                    <div className="profile_text">{firstName}</div>
                    <b>Last Name:</b>
                    <br/>
                    <div className="profile_text">{lastName}</div>
                    <b>Verification Status:</b>
                    <br/>
                    <div className="profile_text">{checkStatus()}</div>
                </div>
            </div>);
    }

    function information(){
        if (!loaded) {
            return (
                <div className="info_container">
                    <div className="profile">
                        <div className="profile_icon"><UserRound aria-hidden="true"/></div>
                        <button className="update_button"> Edit Profile </button>
                    </div>
                    <div className="profile_info">
                        <b>Username:</b>
                        <br/>
                        <div className="loading_profile_text">{empty}</div>
                        <br/>
                        <b>First Name:</b>
                        <br/>
                        <div className="loading_profile_text">{empty}</div>
                        <br/>
                        <b>Last Name:</b>
                        <br/>
                        <div className="loading_profile_text">{empty}</div>
                        <br/>
                        <b>Verification Status:</b>
                        <br/>
                        <div className="loading_profile_text">{empty}</div>
                    </div>
                </div>);
        }

        else {
            return (
                <div className="info_container">
                    <div className="profile">
                        <div className="profile_icon"><UserRound aria-hidden="true"/></div>
                        <button className="update_button"> Edit Profile </button>
                    </div>
                    <div className="profile_info">
                        <b>Username:</b>
                        <br/>
                        <div className="profile_text">{username}</div>
                        <b>First Name:</b>
                        <br/>
                        <div className="profile_text">{firstName}</div>
                        <b>Last Name:</b>
                        <br/>
                        <div className="profile_text">{lastName}</div>
                        <b>Verification Status:</b>
                        <br/>
                        <div className="profile_text">{checkStatus()}</div>
                    </div>
                </div>);
        }
    }

  return (
      <section className="page">
          <header className="page-header">
              <div>
                  <h1>Profile</h1>
                  <p>Control how you're identified by others and review your verification status</p>
              </div>
          </header>
        <div className="profile_card">
            {mainPage()}
        </div>
      </section>

  );
}

