import { useEffect, useRef, useState } from "react";

import type { ExternalLoginProvider } from "../api/contracts";

export const externalProviderLabels: Record<ExternalLoginProvider, string> = {
  microsoft: "Microsoft",
  wechat_website: "微信网站授权",
  qq: "QQ",
};

export function ExternalLoginMethods({ providers, disabled = false }: {
  providers: ExternalLoginProvider[];
  disabled?: boolean;
}) {
  const [starting, setStarting] = useState<ExternalLoginProvider | null>(null);
  const startingRef = useRef(false);
  useEffect(() => {
    function restorePage() {
      startingRef.current = false;
      setStarting(null);
    }
    window.addEventListener("pageshow", restorePage);
    return () => window.removeEventListener("pageshow", restorePage);
  }, []);
  const available = (Object.keys(externalProviderLabels) as ExternalLoginProvider[])
    .filter((provider) => providers.includes(provider));
  if (!available.length) return null;
  return (
    <section className="external-login-methods" aria-label="其他登录方式">
      <p>首次登录需验证邮箱并绑定，之后可直接进入同一资料库。</p>
      <div>
        {available.map((provider) => (
          <form
            key={provider}
            method="post"
            action={`/api/v1/auth/external/${provider}/start`}
            onSubmit={(event) => {
              if (disabled || startingRef.current) {
                event.preventDefault();
                return;
              }
              startingRef.current = true;
              setStarting(provider);
            }}
          >
            <button className="external-login-method" type="submit" disabled={disabled || starting !== null}>
              {starting === provider ? "正在跳转…" : `使用 ${externalProviderLabels[provider]} 登录`}
            </button>
          </form>
        ))}
      </div>
    </section>
  );
}
