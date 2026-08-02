import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

const STORAGE_KEY = "trademind_onboarding_done";

export function useOnboarding() {
  const isDone = () => localStorage.getItem(STORAGE_KEY) === "1";
  const markDone = () => localStorage.setItem(STORAGE_KEY, "1");
  return { isDone, markDone };
}

type StepConfig = {
  titleKey: string;
  bodyKey: string;
  actionKey?: string;
  actionPath?: string;
};

const STEPS: StepConfig[] = [
  { titleKey: "onboarding.step1Title", bodyKey: "onboarding.step1Body" },
  { titleKey: "onboarding.step2Title", bodyKey: "onboarding.step2Body", actionKey: "onboarding.goToSources", actionPath: "/sources" },
  { titleKey: "onboarding.step3Title", bodyKey: "onboarding.step3Body", actionKey: "onboarding.goToAsk",    actionPath: "/ask" },
];

type Props = { onDone: () => void };

export function OnboardingModal({ onDone }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);

  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;
  const total = STEPS.length;

  const finish = () => {
    localStorage.setItem(STORAGE_KEY, "1");
    onDone();
  };

  const goAction = (path: string) => {
    localStorage.setItem(STORAGE_KEY, "1");
    onDone();
    navigate(path);
  };

  const overlay: React.CSSProperties = {
    position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
    zIndex: 300, display: "flex", alignItems: "center", justifyContent: "center",
  };
  const modal: React.CSSProperties = {
    background: "#fff", borderRadius: "12px", padding: "2rem 2.25rem",
    width: "min(480px, 92vw)", boxShadow: "0 12px 40px rgba(0,0,0,0.2)",
    fontFamily: "system-ui, sans-serif",
  };
  const btn = (bg: string, color = "#fff"): React.CSSProperties => ({
    padding: "0.5rem 1.2rem", background: bg, color, border: "none",
    borderRadius: "6px", cursor: "pointer", fontWeight: 600, fontSize: "0.875rem",
  });

  return (
    <div style={overlay}>
      <div style={modal}>
        {/* Step indicator */}
        <div style={{ display: "flex", gap: "6px", marginBottom: "1.5rem" }}>
          {STEPS.map((_, i) => (
            <div
              key={i}
              style={{
                height: "4px", flex: 1, borderRadius: "2px",
                background: i <= step ? "#1a1a2e" : "#e0e0e0",
                transition: "background 0.25s",
              }}
            />
          ))}
        </div>

        <p style={{ margin: "0 0 0.3rem", fontSize: "0.75rem", color: "#aaa" }}>
          {t("onboarding.stepOf", { current: step + 1, total })}
        </p>
        <h2 style={{ margin: "0 0 0.85rem", fontSize: "1.2rem", color: "#1a1a2e" }}>
          {t(current.titleKey)}
        </h2>
        <p style={{ margin: "0 0 1.5rem", fontSize: "0.9rem", color: "#555", lineHeight: 1.6 }}>
          {t(current.bodyKey)}
        </p>

        <div style={{ display: "flex", gap: "0.5rem", justifyContent: "space-between", alignItems: "center" }}>
          <button onClick={finish} style={{ background: "none", border: "none", cursor: "pointer", fontSize: "0.8rem", color: "#aaa" }}>
            {t("onboarding.skip")}
          </button>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            {step > 0 && (
              <button onClick={() => setStep(s => s - 1)} style={{ ...btn("transparent", "#555"), border: "1px solid #ddd" }}>
                {t("onboarding.back")}
              </button>
            )}
            {current.actionKey && current.actionPath && (
              <button onClick={() => goAction(current.actionPath!)} style={btn("#1565c0")}>
                {t(current.actionKey)}
              </button>
            )}
            {isLast ? (
              <button onClick={finish} style={btn("#1a1a2e")}>
                {t("onboarding.finish")}
              </button>
            ) : (
              <button onClick={() => setStep(s => s + 1)} style={btn("#1a1a2e")}>
                {t("onboarding.next")}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
