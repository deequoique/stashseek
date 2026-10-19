import type { PanelDeps } from "./deps";

/** Opens the public privacy policy of this build's API origin in a new tab. Renders nothing without a single API origin. */
export function PrivacyLink({ deps }: { deps: Pick<PanelDeps, "privacyUrl"> }) {
  let href: string;
  try {
    href = deps.privacyUrl();
  } catch {
    return null;
  }
  return <a className="text-button sp-privacy-link" href={href} target="_blank" rel="noopener noreferrer">隐私政策</a>;
}
