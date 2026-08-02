import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./i18n/index";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
