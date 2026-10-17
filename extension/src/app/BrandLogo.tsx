import logoUrl from "../assets/stashseek-chat-logo.png";

interface BrandLogoProps {
  className?: string;
}

export function BrandLogo({ className }: BrandLogoProps) {
  return (
    <img
      alt=""
      aria-hidden="true"
      className={["brand-logo", className].filter(Boolean).join(" ")}
      decoding="async"
      draggable="false"
      height="1254"
      src={logoUrl}
      width="1254"
    />
  );
}
