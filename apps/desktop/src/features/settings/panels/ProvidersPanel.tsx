/** AI Provider Tab：模型、费用与数据范围；密钥一律 password 控件。 */
import type { SettingsConfig } from "@/stores/useSettingsStore";

export function ProvidersPanel({
  settings,
  update,
  showKeys,
  onToggleKey,
  busy,
  onCheck,
}: {
  settings: SettingsConfig;
  update: (patch: Partial<SettingsConfig>) => void;
  showKeys: Record<string, boolean>;
  onToggleKey: (field: string) => void;
  busy: boolean;
  onCheck: () => void;
}) {
  const llm = settings.llm;
  const asr = settings.asr;
  const tts = settings.tts;
  return (
    <>
      <div className="form-row">
        <label htmlFor="aiProvider">AI Provider</label>
        <select
          id="aiProvider"
          className="select"
          value={llm.provider}
          onChange={(e) =>
            update({ llm: { ...llm, provider: e.target.value as SettingsConfig["llm"]["provider"] } })
          }
        >
          <option value="cloud">cloud</option>
          <option value="openai-compatible">openai-compatible</option>
          <option value="ollama">ollama</option>
        </select>
      </div>
      <div className="form-row">
        <label htmlFor="aiModel">模型</label>
        <input
          id="aiModel"
          className="field w-full" 
          value={llm.model}
          onChange={(e) => update({ llm: { ...llm, model: e.target.value } })}
        />
      </div>

      <KeyField
        id="aiApiKey"
        label="API Key"
        value={llm.apiKey}
        visible={!!showKeys.aiApiKey}
        onToggle={() => onToggleKey("aiApiKey")}
        onChange={(v) => update({ llm: { ...llm, apiKey: v } })}
      />
      <div className="form-row">
        <label htmlFor="aiBaseUrl">Base URL</label>
        <input
          id="aiBaseUrl"
          className="field w-full" 
          value={llm.baseUrl}
          onChange={(e) => update({ llm: { ...llm, baseUrl: e.target.value } })}
        />
      </div>
      <div className="form-row">
        <label htmlFor="aiCostPer1k">费用 / 1k tokens</label>
        <input
          id="aiCostPer1k"
          className="field"
          value={llm.costPer1k}
          onChange={(e) => update({ llm: { ...llm, costPer1k: e.target.value } })}
        />
      </div>

      {/* 采样参数 */}
      <div className="form-row">
        <label htmlFor="temperature">Temperature</label>
        <input
          id="temperature"
          className="field"
          type="number"
          step="0.1"
          min="0"
          max="2"
          value={llm.sampling.temperature}
          onChange={(e) =>
            update({
              llm: { ...llm, sampling: { ...llm.sampling, temperature: Number(e.target.value) } },
            })
          }
        />
      </div>
      <div className="form-row">
        <label htmlFor="topP">Top P</label>
        <input
          id="topP"
          className="field"
          type="number"
          step="0.05"
          min="0"
          max="1"
          value={llm.sampling.topP}
          onChange={(e) =>
            update({ llm: { ...llm, sampling: { ...llm.sampling, topP: Number(e.target.value) } } })
          }
        />
      </div>
      <div className="form-row">
        <label htmlFor="maxTokens">Max Tokens</label>
        <input
          id="maxTokens"
          className="field"
          type="number"
          step="1"
          min="1"
          value={llm.sampling.maxTokens}
          onChange={(e) =>
            update({
              llm: { ...llm, sampling: { ...llm.sampling, maxTokens: Number(e.target.value) } },
            })
          }
        />
      </div>

      <div className="form-row">
        <label htmlFor="asrProvider">ASR Provider</label>
        <select
          id="asrProvider"
          className="select"
          value={asr.provider}
          onChange={(e) =>
            update({ asr: { ...asr, provider: e.target.value as SettingsConfig["asr"]["provider"] } })
          }
        >
          <option value="local">local</option>
          <option value="cloud">cloud</option>
        </select>
      </div>
      <KeyField
        id="asrApiKey"
        label="ASR API Key"
        value={asr.apiKey}
        visible={!!showKeys.asrApiKey}
        onToggle={() => onToggleKey("asrApiKey")}
        onChange={(v) => update({ asr: { ...asr, apiKey: v } })}
      />
      <div className="form-row">
        <label htmlFor="asrBaseUrl">ASR Base URL</label>
        <input
          id="asrBaseUrl"
          className="field w-full" 
          value={asr.baseUrl}
          onChange={(e) => update({ asr: { ...asr, baseUrl: e.target.value } })}
        />
      </div>

      <div className="form-row">
        <label htmlFor="ttsProvider">TTS Provider</label>
        <select
          id="ttsProvider"
          className="select"
          value={tts.provider}
          onChange={(e) =>
            update({ tts: { ...tts, provider: e.target.value as SettingsConfig["tts"]["provider"] } })
          }
        >
          <option value="local">local</option>
          <option value="cloud">cloud</option>
        </select>
      </div>
      <KeyField
        id="ttsApiKey"
        label="TTS API Key"
        value={tts.apiKey}
        visible={!!showKeys.ttsApiKey}
        onToggle={() => onToggleKey("ttsApiKey")}
        onChange={(v) => update({ tts: { ...tts, apiKey: v } })}
      />
      <div className="form-row">
        <label htmlFor="ttsBaseUrl">TTS Base URL</label>
        <input
          id="ttsBaseUrl"
          className="field w-full" 
          value={tts.baseUrl}
          onChange={(e) => update({ tts: { ...tts, baseUrl: e.target.value } })}
        />
      </div>
      <div className="form-row">
        <label htmlFor="ttsModel">TTS 模型</label>
        <input
          id="ttsModel"
          className="field"
          value={tts.model}
          onChange={(e) => update({ tts: { ...tts, model: e.target.value } })}
        />
      </div>

      <div className="empty">
        <h2>检查配置</h2>
        <p>默认文本模型：STEPFUN step-3.7；语音：StepAudio / Edge TTS。每次任务开始前展示模型、预计费用与上传范围。</p>
        <button type="button" className="btn" onClick={onCheck} disabled={busy} aria-busy={busy}>
          检查当前配置
        </button>
      </div>
    </>
  );
}

/* ---------- 数据与存储 ---------- */
export function KeyField({
  id,
  label,
  value,
  visible,
  onToggle,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  visible: boolean;
  onToggle: () => void;
  onChange: (v: string) => void;
}) {
  return (
    <div className="form-row">
      <label htmlFor={id}>{label}</label>
      <div className="password-wrap">
        <input
          id={id}
          className="field"
          type={visible ? "text" : "password"}
          autoComplete="off"
          value={value}
          placeholder="••••••••"
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="pw-toggle"
          aria-label={visible ? "隐藏密钥" : "显示密钥"}
          aria-pressed={visible}
          onClick={onToggle}
        >
          {visible ? "隐藏" : "显示"}
        </button>
      </div>
    </div>
  );
}

