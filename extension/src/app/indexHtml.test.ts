/// <reference types="node" />

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("Web document shell", () => {
  it("ships an explicit favicon instead of triggering the browser default 404", () => {
    const html = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
    const favicon = resolve(process.cwd(), "public/favicon.svg");

    expect(html).toMatch(/<link\s+rel="icon"\s+type="image\/svg\+xml"\s+href="\/favicon\.svg"\s*\/>/);
    expect(existsSync(favicon)).toBe(true);
  });

  it("uses smooth same-document anchor scrolling while honoring reduced motion", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

    expect(css).toMatch(/html\s*\{[^}]*scroll-behavior:\s*smooth;/);
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*scroll-behavior:\s*auto\s*!important/,
    );
  });

  it("uses one wireframe treatment and a top-left offset for all audience icons", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    const iconRule = css.match(/\.showcase-audience__icon\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(iconRule).toMatch(/top:\s*-0\.5rem;/);
    expect(iconRule).toMatch(/left:\s*-0\.5rem;/);
    expect(iconRule).toMatch(/border:\s*1px solid rgba\(21,\s*24,\s*21,\s*0\.34\);/);
    expect(iconRule).toMatch(/background:\s*transparent;/);
    expect(iconRule).toMatch(/color:\s*var\(--showcase-ink\);/);
    expect(css).not.toContain("--audience-icon-background");
    expect(css).not.toContain("--audience-icon-color");
  });

  it("animates demo answer segments without CSP-blocked inline styles", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    const showcase = readFileSync(resolve(process.cwd(), "src/showcase/ShowcasePage.tsx"), "utf8");

    expect(css).toMatch(
      /\.demo-typewriter\s*\{[^}]*animation:[^;}]*demo-segment-in[^;}]*forwards;/,
    );
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*\.demo-typewriter\s*\{[^}]*animation:\s*none\s*!important;/,
    );
    expect(showcase).not.toContain("style={");
    expect(showcase).not.toContain("style={{");
    expect(showcase).not.toContain("--typing-index");
    expect(showcase).not.toContain("--demo-subtitle-duration");
    expect(css).not.toMatch(/\.demo-evidence\s*\{[^}]*opacity:\s*0;/);
    expect(css).not.toMatch(/\.demo-reset\s*\{[^}]*opacity:\s*0;/);
  });

  it("keeps local demo authentication out of the production route tree", () => {
    const app = readFileSync(resolve(process.cwd(), "src/app/App.tsx"), "utf8");
    const login = readFileSync(resolve(process.cwd(), "src/auth/LoginPage.tsx"), "utf8");

    expect(app).not.toContain("VITE_LOCAL_DEMO_DIRECT_LOGIN");
    expect(login).not.toContain("directDemoLogin");
    expect(login).not.toMatch(/authenticated:\s*true/);
  });

  it("uses a moderate login exit transition with a reduced-motion fallback", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

    expect(css).toMatch(/\.login-card\s*\{[^}]*transition:[^;}]*420ms/);
    expect(css).toMatch(/\.login-page--leaving \.login-card\s*\{[^}]*opacity:\s*0;[^}]*transform:/);
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*\.login-card\s*\{[^}]*transition-duration:\s*80ms\s*!important;/,
    );
  });

  it("uses one moderate route transition and removes it for reduced motion", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

    expect(css).toMatch(/\.page-container,[^}]*\.login-page,[^}]*\.showcase-page\s*\{[^}]*view-transition-name:\s*app-route;/);
    expect(css).toMatch(/::view-transition-old\(app-route\)\s*\{[^}]*route-page-out[^}]*220ms/);
    expect(css).toMatch(/::view-transition-new\(app-route\)\s*\{[^}]*route-page-in[^}]*380ms/);
    expect(css).toMatch(/\.route-transition--entering[^}]*\{[^}]*animation:\s*route-page-in 380ms/);
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*::view-transition-old\(app-route\),[\s\S]*::view-transition-new\(app-route\),[\s\S]*\.route-transition--entering[^}]*\{[^}]*animation:\s*none\s*!important;/,
    );
  });

  it("keeps frequent subtitle controls at a mobile-safe touch size", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

    expect(css).toMatch(/\.demo-subtitle-language button\s*\{[^}]*width:\s*2\.75rem;[^}]*height:\s*2\.75rem;/);
    expect(css).toMatch(/\.demo-subtitle-language button > span\s*\{[^}]*width:\s*1\.75rem;[^}]*height:\s*1\.75rem;/);
    expect(css).toMatch(/\.demo-subtitle-language button\[aria-pressed="true"\] > span\s*\{[^}]*background:\s*var\(--showcase-signal\);/);
    expect(css).toMatch(/\.transcript-list a\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/);
    expect(css).not.toMatch(/\.demo-subtitle-language button\s*\{[^}]*min-height:\s*1\.(?:35|5)rem;/);
  });

  it("keeps the interactive hero covers layered and styles their mobile captions", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

    expect(css).toMatch(
      /\.instrument-cover\.is-front\s*\{[^}]*--cover-z:\s*3rem;[^}]*z-index:\s*4;/,
    );
    expect(css).toContain(".instrument-cover__caption { grid-template-columns:");
    expect(css).not.toContain(".instrument-cover figcaption");
  });

  it("skins native library selects with the shared paper-and-ink control treatment", () => {
    const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

    expect(css).toMatch(/\.select-field\s*\{[^}]*position:\s*relative;[^}]*min-width:\s*0;/);
    expect(css).toMatch(/\.select-field::after\s*\{[^}]*pointer-events:\s*none;/);
    expect(css).toMatch(/\.select-field select\s*\{[^}]*appearance:\s*none;/);
    expect(css).toMatch(
      /\.select-field select\s*\{[^}]*border-radius:\s*0\.9rem 0\.9rem 0\.9rem 0\.28rem;/,
    );
    expect(css).toMatch(/\.select-field select:focus-visible\s*\{[^}]*outline:\s*3px solid/);
    expect(css).toMatch(/\.select-field select:disabled\s*\{[^}]*cursor:\s*not-allowed;/);
  });
});
