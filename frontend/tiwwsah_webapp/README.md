# ScamAi frontend

Next.js frontend for the browser-based scam-awareness simulator.

## Run locally

```bash
#to get started first install packages and run nextjs server with:
npm install
npm run dev
```

Then open [http://localhost:3000](http://localhost:3000).

## development notes:

We store the users logged in account id in the browsers cookies which is used to pull their required data from the backend endpoints, we attach the cookie in the header of every request to the backend.
