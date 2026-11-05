import { useCallback, useEffect, useRef, useState } from "react";
import { getHardwareProfile, listHardwareProfiles, upsertHardwareProfile } from "../../api/memory";
import { useI18n } from "../../i18n";
import type { HardwareProfile, HardwareProfileInput } from "../../types/memory";
import { EmptyState } from "../shared/EmptyState";

interface ProfileDraft {
  id?: string;
  name: string;
  mcu: string;
  package: string;
  voltage: string;
  clock: string;
  toolchain: string;
  aliases: string;
  pins: string;
  peripherals: string;
  sources: string;
  rejected: string;
  confidence: string;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function emptyDraft(): ProfileDraft {
  return {
    name: "", mcu: "", package: "", voltage: "", clock: "", toolchain: "",
    aliases: "", pins: "{}", peripherals: "[]", sources: "[]", rejected: "[]", confidence: "0.7",
  };
}

function profileToDraft(profile: HardwareProfile): ProfileDraft {
  return {
    id: profile.id,
    name: profile.name ?? "",
    mcu: profile.mcu ?? "",
    package: profile.package ?? "",
    voltage: profile.voltage ?? "",
    clock: profile.clock ?? "",
    toolchain: profile.toolchain ?? "",
    aliases: Array.isArray(profile.aliases) ? profile.aliases.join("\n") : "",
    pins: JSON.stringify(profile.pins ?? {}, null, 2),
    peripherals: JSON.stringify(profile.peripherals ?? [], null, 2),
    sources: JSON.stringify(profile.sources ?? [], null, 2),
    rejected: JSON.stringify(profile.rejected ?? [], null, 2),
    confidence: String(profile.confidence ?? 0.7),
  };
}

function splitAliases(value: string): string[] {
  const seen = new Set<string>();
  const aliases: string[] = [];
  for (const part of value.split(/[\n,，]/)) {
    const alias = part.trim();
    if (!alias || seen.has(alias)) continue;
    seen.add(alias);
    aliases.push(alias);
  }
  return aliases;
}

function parseJsonObject(text: string, fieldLabel: string, invalidLabel: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(`${invalidLabel}: ${fieldLabel}`);
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${invalidLabel}: ${fieldLabel}`);
  }
  return parsed as Record<string, unknown>;
}

function parseJsonArray(text: string, fieldLabel: string, invalidLabel: string): unknown[] {
  const trimmed = text.trim();
  if (!trimmed) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(`${invalidLabel}: ${fieldLabel}`);
  }
  if (!Array.isArray(parsed)) throw new Error(`${invalidLabel}: ${fieldLabel}`);
  return parsed;
}

function displayValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatDate(value: string | null | undefined, lang: string): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { hour12: false });
}

function InfoRow({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div style={{ display: "flex", gap: 8, fontSize: 12 }}>
      <span style={{ color: "var(--muted-fg)", minWidth: 76 }}>{label}</span>
      <span style={{ wordBreak: "break-word" }}>{value}</span>
    </div>
  );
}

function ChipList({ items }: { items: unknown[] }) {
  if (items.length === 0) return <span style={{ fontSize: 12, color: "var(--muted-fg)" }}>—</span>;
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {items.map((item, index) => (
        <span
          key={`${index}-${displayValue(item)}`}
          style={{
            fontSize: 11,
            padding: "1px 7px",
            borderRadius: 10,
            border: "1px solid var(--border)",
            color: "var(--muted-fg)",
            wordBreak: "break-all",
          }}
        >
          {displayValue(item)}
        </span>
      ))}
    </div>
  );
}

export function HardwareProfilesPanel() {
  const { t, lang } = useI18n();
  const [profiles, setProfiles] = useState<HardwareProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [selected, setSelected] = useState<HardwareProfile | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestRef = useRef(0);
  const detailRequestRef = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    try {
      const items = await listHardwareProfiles({ q: debouncedSearch, limit: 100 });
      if (requestId === requestRef.current) setProfiles(items);
    } catch (err) {
      if (requestId === requestRef.current) setError(errorMessage(err));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [debouncedSearch]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectProfile = useCallback(async (profile: HardwareProfile) => {
    const requestId = ++detailRequestRef.current;
    setSelected(profile);
    setDetailLoading(true);
    setDetailError(null);
    setDraft(null);
    setFormError(null);
    try {
      const full = await getHardwareProfile(profile.id);
      if (requestId === detailRequestRef.current) setSelected(full);
    } catch (err) {
      if (requestId === detailRequestRef.current) setDetailError(errorMessage(err));
    } finally {
      if (requestId === detailRequestRef.current) setDetailLoading(false);
    }
  }, []);

  const handleSave = async () => {
    if (!draft) return;
    const name = draft.name.trim();
    if (!name) {
      setFormError(t("hardwareNameRequired"));
      return;
    }
    const confidence = Number(draft.confidence);
    if (Number.isNaN(confidence) || confidence < 0 || confidence > 1) {
      setFormError(t("memoryConfidenceInvalid"));
      return;
    }
    let pins: Record<string, unknown>;
    let peripherals: unknown[];
    let sources: unknown[];
    let rejected: unknown[];
    try {
      const invalid = t("hardwareJsonInvalid");
      pins = parseJsonObject(draft.pins, t("hardwarePins"), invalid);
      peripherals = parseJsonArray(draft.peripherals, t("hardwarePeripherals"), invalid);
      sources = parseJsonArray(draft.sources, t("hardwareSources"), invalid);
      rejected = parseJsonArray(draft.rejected, t("hardwareRejected"), invalid);
    } catch (err) {
      setFormError(errorMessage(err));
      return;
    }
    const payload: HardwareProfileInput = {
      ...(draft.id ? { id: draft.id } : {}),
      name,
      aliases: splitAliases(draft.aliases),
      mcu: draft.mcu.trim(),
      package: draft.package.trim(),
      voltage: draft.voltage.trim(),
      clock: draft.clock.trim(),
      toolchain: draft.toolchain.trim(),
      pins,
      peripherals,
      sources,
      rejected,
      confidence,
    };
    setBusy(true);
    setFormError(null);
    try {
      const saved = await upsertHardwareProfile(payload);
      setProfiles((prev) => {
        const exists = prev.some((profile) => profile.id === saved.id);
        return exists ? prev.map((profile) => (profile.id === saved.id ? saved : profile)) : [saved, ...prev];
      });
      setSelected(saved);
      setDraft(null);
      setNotice(t("hardwareSaved"));
    } catch (err) {
      setFormError(`${t("hardwareSaveFailed")}: ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const pinEntries = selected ? Object.entries(selected.pins ?? {}) : [];

  return (
    <div>
      <div
        role="note"
        style={{
          fontSize: 12,
          color: "var(--muted-fg)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: "8px 12px",
          marginBottom: 14,
        }}
      >
        {t("hardwareNotice")}
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <input
          className="form-input"
          style={{ flex: "1 1 240px" }}
          aria-label={t("hardwareSearchPlaceholder")}
          placeholder={t("hardwareSearchPlaceholder")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button
          type="button"
          className="settings-btn primary"
          onClick={() => {
            setSelected(null);
            setDraft(emptyDraft());
            setFormError(null);
            setNotice(null);
          }}
        >
          {t("hardwareNew")}
        </button>
      </div>

      {notice && <div role="status" style={{ fontSize: 12, color: "var(--success)", marginBottom: 10 }}>{notice}</div>}
      {formError && <div role="alert" style={{ fontSize: 12, color: "var(--danger)", marginBottom: 10 }}>{formError}</div>}

      {draft && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "14px 16px", marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
            <div style={{ flex: "1 1 220px" }}>
              <label htmlFor="hardware-name" className="field-label">{t("hardwareName")}</label>
              <input
                id="hardware-name"
                className="form-input"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </div>
            <div style={{ flex: "1 1 160px" }}>
              <label htmlFor="hardware-mcu" className="field-label">{t("hardwareMcu")}</label>
              <input
                id="hardware-mcu"
                className="form-input"
                value={draft.mcu}
                onChange={(e) => setDraft({ ...draft, mcu: e.target.value })}
              />
            </div>
            <div style={{ flex: "0 0 120px" }}>
              <label htmlFor="hardware-confidence" className="field-label">{t("memoryConfidence")}</label>
              <input
                id="hardware-confidence"
                className="form-input"
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={draft.confidence}
                onChange={(e) => setDraft({ ...draft, confidence: e.target.value })}
              />
            </div>
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
            {([
              ["hardware-package", t("hardwarePackage"), "package"],
              ["hardware-voltage", t("hardwareVoltage"), "voltage"],
              ["hardware-clock", t("hardwareClock"), "clock"],
              ["hardware-toolchain", t("hardwareToolchain"), "toolchain"],
            ] as const).map(([id, label, key]) => (
              <div key={id} style={{ flex: "1 1 150px" }}>
                <label htmlFor={id} className="field-label">{label}</label>
                <input
                  id={id}
                  className="form-input"
                  value={draft[key]}
                  onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                />
              </div>
            ))}
          </div>
          <label htmlFor="hardware-aliases" className="field-label">{t("hardwareAliases")}</label>
          <textarea
            id="hardware-aliases"
            className="settings-textarea"
            rows={2}
            value={draft.aliases}
            placeholder={t("hardwareAliasesPlaceholder")}
            onChange={(e) => setDraft({ ...draft, aliases: e.target.value })}
          />
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 10 }}>
            {([
              ["hardware-pins", t("hardwarePins"), "pins"],
              ["hardware-peripherals", t("hardwarePeripherals"), "peripherals"],
              ["hardware-sources", t("hardwareSources"), "sources"],
              ["hardware-rejected", t("hardwareRejected"), "rejected"],
            ] as const).map(([id, label, key]) => (
              <div key={id} style={{ flex: "1 1 240px" }}>
                <label htmlFor={id} className="field-label">{label} (JSON)</label>
                <textarea
                  id={id}
                  className="settings-textarea"
                  rows={key === "pins" ? 5 : 3}
                  value={draft[key]}
                  spellCheck={false}
                  onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                />
              </div>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button type="button" className="verify-btn primary" disabled={busy} onClick={() => void handleSave()}>
              {busy ? t("memorySaving") : t("hardwareSave")}
            </button>
            <button type="button" className="verify-btn" disabled={busy} onClick={() => { setDraft(null); setFormError(null); }}>
              {t("cancel")}
            </button>
          </div>
        </div>
      )}

      {loading && <div style={{ fontSize: 12, color: "var(--muted-fg)", padding: "12px 0" }}>{t("memoryLoading")}</div>}

      {!loading && error && (
        <div role="alert" style={{ fontSize: 12, color: "var(--danger)", padding: "12px 0" }}>
          {t("hardwareLoadFailed")}: {error}
          <button type="button" className="verify-btn" style={{ marginLeft: 8 }} onClick={() => void load()}>
            {t("retry")}
          </button>
        </div>
      )}

      {!loading && !error && profiles.length === 0 && (
        <EmptyState size="md" title={t("hardwareEmpty")} desc={t("hardwareEmptyDesc")} />
      )}

      {!loading && !error && profiles.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 16 }}>
          {profiles.map((profile) => (
            <button
              key={profile.id}
              type="button"
              className={`settings-btn${selected?.id === profile.id ? " primary" : ""}`}
              style={{ textAlign: "left", padding: "8px 12px" }}
              onClick={() => void selectProfile(profile)}
            >
              <span style={{ display: "block", fontSize: 13 }}>{profile.name}</span>
              <span style={{ display: "block", fontSize: 11, color: "var(--muted-fg)" }}>
                {[profile.mcu, Array.isArray(profile.aliases) ? profile.aliases.join(" / ") : ""].filter(Boolean).join(" · ")}
              </span>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "14px 16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
            <h4 style={{ fontSize: 14, fontWeight: 600 }}>{selected.name}</h4>
            <span style={{ fontSize: 11, color: "var(--muted-fg)" }}>
              {t("memoryConfidence")} {Number.isFinite(selected.confidence) ? selected.confidence.toFixed(2) : "—"}
              {" · "}{t("hardwareUseCount")} {selected.use_count ?? 0}
            </span>
            <button
              type="button"
              className="verify-btn"
              style={{ marginLeft: "auto" }}
              onClick={() => { setDraft(profileToDraft(selected)); setFormError(null); setNotice(null); }}
            >
              {t("edit")}
            </button>
          </div>

          {detailLoading && <div style={{ fontSize: 12, color: "var(--muted-fg)" }}>{t("memoryLoading")}</div>}
          {detailError && (
            <div role="alert" style={{ fontSize: 12, color: "var(--danger)", marginBottom: 8 }}>
              {t("hardwareDetailFailed")}: {detailError}
            </div>
          )}

          <div style={{ display: "grid", gap: 4, marginBottom: 12 }}>
            <InfoRow label={t("hardwareMcu")} value={selected.mcu} />
            <InfoRow label={t("hardwarePackage")} value={selected.package} />
            <InfoRow label={t("hardwareVoltage")} value={selected.voltage} />
            <InfoRow label={t("hardwareClock")} value={selected.clock} />
            <InfoRow label={t("hardwareToolchain")} value={selected.toolchain} />
            <InfoRow label={t("hardwareLastUsed")} value={formatDate(selected.last_used_at, lang)} />
          </div>

          <div style={{ marginBottom: 10 }}>
            <div className="field-label">{t("hardwareAliases")}</div>
            <ChipList items={Array.isArray(selected.aliases) ? selected.aliases : []} />
          </div>

          <div style={{ marginBottom: 10 }}>
            <div className="field-label">{t("hardwarePins")}</div>
            {pinEntries.length === 0 ? (
              <span style={{ fontSize: 12, color: "var(--muted-fg)" }}>—</span>
            ) : (
              <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
                {pinEntries.map(([pin, value], index) => (
                  <div
                    key={pin}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "minmax(80px, 140px) 1fr",
                      gap: 8,
                      padding: "5px 8px",
                      fontSize: 12,
                      borderTop: index === 0 ? "none" : "1px solid var(--border)",
                    }}
                  >
                    <span style={{ fontFamily: "var(--font-mono)", color: "var(--muted-fg)" }}>{pin}</span>
                    <span style={{ wordBreak: "break-word" }}>{displayValue(value)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div style={{ marginBottom: 10 }}>
            <div className="field-label">{t("hardwarePeripherals")}</div>
            <ChipList items={Array.isArray(selected.peripherals) ? selected.peripherals : []} />
          </div>

          <div style={{ marginBottom: 10 }}>
            <div className="field-label">{t("hardwareSources")}</div>
            <ChipList items={Array.isArray(selected.sources) ? selected.sources : []} />
          </div>

          <div>
            <div className="field-label">{t("hardwareRejected")}</div>
            <ChipList items={Array.isArray(selected.rejected) ? selected.rejected : []} />
          </div>
        </div>
      )}
    </div>
  );
}
