"use client";

import { useEffect, useRef } from "react";
import { Lock, LogOut, ShieldCheck } from "lucide-react";

export interface AuthState {
  loaded: boolean;
  authenticated: boolean;
  isAdmin: boolean;
  email: string | null;
  name: string | null;
  picture: string | null;
  googleClientId: string;
}

interface AuthControlsProps {
  auth: AuthState;
  isDark: boolean;
  compact?: boolean;
  onGoogleCredential: (credential: string) => void;
  onLogout: () => void;
}

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (cfg: {
            client_id: string;
            callback: (resp: { credential?: string }) => void;
            auto_select?: boolean;
          }) => void;
          renderButton: (
            el: HTMLElement,
            opts: {
              theme?: "outline" | "filled_blue" | "filled_black";
              size?: "large" | "medium" | "small";
              shape?: "rectangular" | "pill";
              text?: "signin_with" | "continue_with" | "signin";
              locale?: string;
            }
          ) => void;
        };
      };
    };
  }
}

export function AuthControls({ auth, isDark, compact = true, onGoogleCredential, onLogout }: AuthControlsProps) {
  const btnRef = useRef<HTMLDivElement | null>(null);
  const cbRef = useRef(onGoogleCredential);
  cbRef.current = onGoogleCredential;

  useEffect(() => {
    if (!auth.loaded || auth.isAdmin || !auth.googleClientId) return;

    let cancelled = false;
    const renderBtn = () => {
      if (cancelled || !btnRef.current || !window.google?.accounts?.id) return;
      btnRef.current.innerHTML = "";
      window.google.accounts.id.initialize({
        client_id: auth.googleClientId,
        callback: (resp) => {
          if (resp.credential) cbRef.current(resp.credential);
        },
      });
      window.google.accounts.id.renderButton(btnRef.current, {
        theme: isDark ? "filled_black" : "outline",
        size: compact ? "medium" : "large",
        shape: "pill",
        text: "signin_with",
        locale: "th",
      });
    };

    if (window.google?.accounts?.id) {
      renderBtn();
      return () => {
        cancelled = true;
      };
    }

    const existing = document.querySelector<HTMLScriptElement>('script[src="https://accounts.google.com/gsi/client"]');
    if (existing) {
      existing.addEventListener("load", renderBtn);
      return () => {
        cancelled = true;
        existing.removeEventListener("load", renderBtn);
      };
    }

    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.onload = renderBtn;
    document.head.appendChild(script);

    return () => {
      cancelled = true;
    };
  }, [auth.loaded, auth.isAdmin, auth.googleClientId, isDark, compact]);

  if (!auth.loaded) return null;

  if (auth.isAdmin) {
    return (
      <div className="inline-flex items-center gap-1.5">
        <span className="cc-chip tone-in h-9 rounded-xl px-3" title={auth.email || ""}>
          <ShieldCheck className="h-3.5 w-3.5" />
          <span className="max-w-[160px] truncate">{auth.email}</span>
        </span>
        <button
          type="button"
          onClick={onLogout}
          className="cc-btn h-9 gap-1.5"
          title="ออกจากระบบผู้ดูแล"
        >
          <LogOut className="h-3.5 w-3.5" />
          <span>ออก</span>
        </button>
      </div>
    );
  }

  return (
    <div className={`inline-flex flex-wrap items-center justify-center gap-2.5 ${compact ? "" : "flex-col"}`}>
      {compact && (
        <span className="cc-chip tone-held h-9 rounded-xl px-3">
          <Lock className="h-3.5 w-3.5" />
          <span>สงวนสิทธิ์ผู้ดูแลระบบ</span>
        </span>
      )}
      {auth.googleClientId && <div ref={btnRef} className="inline-flex min-h-[40px] items-center justify-center" />}
    </div>
  );
}
