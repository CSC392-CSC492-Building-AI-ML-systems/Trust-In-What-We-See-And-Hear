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

    async function loadUser() {
        try{
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

    function information(){
        if (!loaded) {
            return (
                <div className="info_container">
                    <div className="profile_icon"><UserRound aria-hidden="true"/></div>
                    <div className="profile_info">

                        <br/>
                        <div className="loading_profile_text">{username}</div>
                        <br/>

                        <br/>
                        <div className="loading_profile_text">{username}</div>
                        <br/>

                        <br/>
                        <div className="loading_profile_text">{username}</div>
                        <br/>
                        <br/>
                        <div className="loading_profile_text">{username}</div>
                    </div>
                </div>);
        }

        else {
            return (
                <div className="info_container">
                    <div className="profile_icon"><UserRound aria-hidden="true"/></div>
                    <div className="profile_info">
                        Username:
                        <br/>
                        <div className="profile_text">{username}</div>
                        <br/>
                        First Name:
                        <br/>
                        <div className="profile_text">{firstName}</div>
                        <br/>
                        Last Name:
                        <br/>
                        <div className="profile_text">{lastName}</div>
                        <br/>
                        Verification Status:<br/>
                        <div className="profile_text">{checkStatus()}</div>
                    </div>
                </div>);
        }
    }

  return (
      <section className="page">
        <div className="profile_card">
            {information()}
        </div>
      </section>

  );
}

