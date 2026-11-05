import { cookies } from "next/headers";

const NEXT_PUBLIC_API_URL = process.env.BACKEND_API_IP;

export type CurrentUser = {
  email: string;
  first_name: string;
  last_name: string;
};

export async function getCurrentUser(): Promise<CurrentUser | null> {
  // WE WILL USE THIS CODE ONCE WE HAVE THE BACKEND ENDPOINT SETUP FOR IT.
  //const cookieStore = await cookies();
  //const response = await fetch(NEXT_PUBLIC_API_URL, {
  //  headers: {
  //    Cookie: cookieStore.toString(),
  //  },
  //  cache: "no-store",
  //});

  //if (response.status === 401) {
  //  return null;
  //}

  //if (!response.ok) {
  //  throw new Error("Unable to retrieve the current user.");
  //}

  //return response.json() as Promise<CurrentUser>;


  return Promise.resolve({
    email: "test@gmail.com", 
    first_name: "Rutwa", 
    last_name: "Engineer"
  }) as Promise<CurrentUser>;
}
