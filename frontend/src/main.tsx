import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { loadAppearance } from "./theme";
import "./styles.css";

const render = () =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

// Wait briefly for the saved theme so the app doesn't paint in the default colors first,
// but never hold the UI back for long (e.g. when the backend is down).
void Promise.race([loadAppearance(), new Promise((r) => setTimeout(r, 1000))]).finally(render);
