import { useState } from "react";
import { api } from "../api";

/** The agent's tool for asking the user multiple-choice questions. */
export const QUESTION_TOOL = "AskUserQuestion";

interface Question {
  question: string;
  header?: string;
  multiSelect?: boolean;
  options?: { label: string; description?: string; preview?: string }[];
}

interface Draft {
  picked: string[];
  typed: string; // an answer in the user's own words
}

const EMPTY: Draft = { picked: [], typed: "" };

/** Questions the agent is waiting on: pick options or type an answer, then send them all at once. */
export function QuestionCard({
  runId,
  approvalId,
  input,
}: {
  runId: number;
  approvalId: string;
  input: { questions?: Question[] };
}) {
  const questions = input.questions ?? [];
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const answerTo = (i: number) => {
    const { picked, typed } = drafts[i] ?? EMPTY;
    return [...picked, typed.trim()].filter(Boolean).join(", ");
  };
  const complete = questions.length > 0 && questions.every((_, i) => answerTo(i));

  const pick = (i: number, q: Question, label: string) =>
    setDrafts((all) => {
      const draft = all[i] ?? EMPTY;
      if (!q.multiSelect) return { ...all, [i]: { picked: [label], typed: "" } };
      const picked = draft.picked.includes(label) ? draft.picked.filter((l) => l !== label) : [...draft.picked, label];
      return { ...all, [i]: { ...draft, picked } };
    });

  // With one choice only, typing an answer replaces the picked option.
  const type = (i: number, q: Question, typed: string) =>
    setDrafts((all) => ({
      ...all,
      [i]: { picked: q.multiSelect || !typed ? (all[i] ?? EMPTY).picked : [], typed },
    }));

  const submit = async (request: () => Promise<unknown>) => {
    setSending(true);
    setError("");
    try {
      await request(); // the decision arrives on the run's stream, which replaces this card
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSending(false);
    }
  };
  const send = () => {
    if (!complete || sending) return;
    const answers = Object.fromEntries(questions.map((q, i) => [q.question, answerTo(i)]));
    void submit(() => api.answer(runId, approvalId, answers));
  };

  return (
    <div className="ev-approval ev-question">
      {questions.map((q, i) => {
        const draft = drafts[i] ?? EMPTY;
        return (
          <fieldset key={i} disabled={sending}>
            <div className="ev-question-text">
              {q.header && <span className="ev-question-chip">{q.header}</span>}
              {q.question}
            </div>
            {q.options?.map((option) => {
              const picked = draft.picked.includes(option.label);
              return (
                <label key={option.label} className={`ev-option ${picked ? "picked" : ""}`}>
                  <input
                    type={q.multiSelect ? "checkbox" : "radio"}
                    name={`${approvalId}-${i}`}
                    checked={picked}
                    onChange={() => pick(i, q, option.label)}
                  />
                  <span>
                    {option.label}
                    {option.description && <small>{option.description}</small>}
                    {option.preview && <pre>{option.preview}</pre>}
                  </span>
                </label>
              );
            })}
            <input
              type="text"
              aria-label={`Your own answer: ${q.question}`}
              placeholder={q.multiSelect ? "Add your own answer…" : "Or type your own answer…"}
              value={draft.typed}
              onChange={(e) => type(i, q, e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) send();
              }}
            />
          </fieldset>
        );
      })}
      <div className="row">
        <button className="btn primary small" disabled={!complete || sending} onClick={send}>
          {questions.length > 1 ? "Send answers" : "Send answer"}
        </button>
        <button
          className="btn small"
          disabled={sending}
          title="Carry on without answering"
          onClick={() => void submit(() => api.decide(runId, approvalId, false))}
        >
          Dismiss
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
