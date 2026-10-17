import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "../styles.css";
import "./sidepanel.css";
import { SidePanel } from "./SidePanel";

const root = document.getElementById("root");
if (!root) throw new Error("Side panel root is missing");

createRoot(root).render(
  <StrictMode>
    <SidePanel />
  </StrictMode>,
);
