import { createBrowserRouter, RouterProvider } from "react-router";
import { Shell } from "./components/Shell";
import { TokenGate } from "./components/TokenGate";
import { LiveProvider } from "./components/live/LiveProvider";
import { Answers } from "./pages/Answers";
import { Approvals } from "./pages/Approvals";
import { FlightDeck } from "./pages/FlightDeck";
import { Inbox } from "./pages/Inbox";
import { MissionControl } from "./pages/MissionControl";
import { Jobs } from "./pages/Jobs";
import { Models } from "./pages/Models";
import { Portals } from "./pages/Portals";
import { Profile } from "./pages/Profile";
import { System } from "./pages/System";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <MissionControl /> },
      { path: "setup", element: <FlightDeck /> },
      { path: "jobs", element: <Jobs /> },
      { path: "approvals", element: <Approvals /> },
      { path: "inbox", element: <Inbox /> },
      { path: "portals", element: <Portals /> },
      { path: "profile", element: <Profile /> },
      { path: "answers", element: <Answers /> },
      { path: "models", element: <Models /> },
      { path: "system", element: <System /> },
      { path: "*", element: <MissionControl /> },
    ],
  },
]);

export function App() {
  return (
    <TokenGate>
      <LiveProvider>
        <RouterProvider router={router} />
      </LiveProvider>
    </TokenGate>
  );
}
