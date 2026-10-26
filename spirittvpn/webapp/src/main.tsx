import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { setupTelegram } from "./tg";
import "./styles.css";

setupTelegram();
if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) document.documentElement.classList.add("reduce-motion");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
