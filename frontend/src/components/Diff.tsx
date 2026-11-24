import { useState } from "react";

export type DiffLine = { type: "hunk" | "add" | "del" | "ctx" | "meta"; text: string; oldNo?: number; newNo?: number };

/** Parse `git diff` output into rows for <DiffTable>. */
export function parseDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = [];
  const header: DiffLine[] = [];
  let oldNo = 0;
  let newNo = 0;
  let inHunk = false;
  let combined = false; // merge conflicts produce a combined diff ("@@@" hunks, two-column +/- markers)
  for (const line of diff.split("\n")) {
    const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (line.startsWith("@@@")) {
      inHunk = combined = true;
      out.push({ type: "hunk", text: line });
    } else if (combined && inHunk && !line.startsWith("diff ")) {
      const marks = line.slice(0, 2);
      out.push({ type: marks.includes("+") ? "add" : marks.includes("-") ? "del" : "ctx", text: line.slice(2) });
    } else if (m) {
      inHunk = true;
      oldNo = Number(m[1]);
      newNo = Number(m[2]);
      out.push({ type: "hunk", text: line });
    } else if (line.startsWith("diff ")) {
      inHunk = false;
    } else if (!inHunk) {
      if (line && !/^(index |--- |\+\+\+ )/.test(line)) header.push({ type: "meta", text: line });
    } else if (line.startsWith("+")) {
      out.push({ type: "add", text: line.slice(1), newNo: newNo++ });
    } else if (line.startsWith("-")) {
      out.push({ type: "del", text: line.slice(1), oldNo: oldNo++ });
    } else if (line.startsWith("\\")) {
      out.push({ type: "meta", text: line });
    } else if (line !== "" || out.length) {
      out.push({ type: "ctx", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  // Drop the empty context line produced by the diff's trailing newline.
  while (out.length && out[out.length - 1].type === "ctx" && out[out.length - 1].text === "") out.pop();
  // Mode changes, renames and binary files have no hunks: show git's description instead.
  return out.some((l) => l.type !== "meta") ? out : header;
}

function splitLines(text: string): string[] {
  if (!text) return [];
  const lines = text.split(/\r?\n/);
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** Above this many cells the LCS table gets slow; show the change as a plain replace instead. */
const MAX_LCS_CELLS = 1_000_000;

/** Line diff of two snippets (an agent's old_string → new_string), with line numbers relative to the snippet. */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = splitLines(before);
  const b = splitLines(after);
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const am = a.slice(pre, a.length - suf);
  const bm = b.slice(pre, b.length - suf);

  const out: DiffLine[] = [];
  let o = 1;
  let n = 1;
  const ctx = (text: string) => out.push({ type: "ctx", text, oldNo: o++, newNo: n++ });
  const del = (text: string) => out.push({ type: "del", text, oldNo: o++ });
  const add = (text: string) => out.push({ type: "add", text, newNo: n++ });

  a.slice(0, pre).forEach(ctx);
  if (am.length * bm.length > MAX_LCS_CELLS) {
    am.forEach(del);
    bm.forEach(add);
  } else {
    // lcs[i][j] = length of the longest common subsequence of am[i..] and bm[j..]
    const w = bm.length + 1;
    const lcs = new Uint32Array((am.length + 1) * w);
    for (let i = am.length - 1; i >= 0; i--) {
      for (let j = bm.length - 1; j >= 0; j--) {
        lcs[i * w + j] = am[i] === bm[j] ? lcs[(i + 1) * w + j + 1] + 1 : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < am.length || j < bm.length) {
      if (i < am.length && j < bm.length && am[i] === bm[j]) {
        ctx(am[i++]);
        j++;
      } else if (j >= bm.length || (i < am.length && lcs[(i + 1) * w + j] >= lcs[i * w + j + 1])) {
        del(am[i++]);
      } else {
        add(bm[j++]);
      }
    }
  }
  a.slice(a.length - suf).forEach(ctx);
  return out;
}

export function diffStats(lines: DiffLine[]) {
  let added = 0;
  let removed = 0;
  for (const l of lines) {
    if (l.type === "add") added++;
    else if (l.type === "del") removed++;
  }
  return { added, removed };
}

export function DiffStat({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="diff-stat">
      {added > 0 && <span className="plus">+{added}</span>}
      {removed > 0 && <span className="minus">−{removed}</span>}
    </span>
  );
}

/** A diff as a table. `limit` shows only the first N rows until the user asks for the rest. */
export function DiffTable({ lines, numbers = true, limit }: { lines: DiffLine[]; numbers?: boolean; limit?: number }) {
  const [all, setAll] = useState(false);
  const shown = limit && !all ? lines.slice(0, limit) : lines;
  return (
    <>
      <table className="diff">
        <tbody>
          {shown.map((l, i) => (
            <tr key={i} className={`d-${l.type}`}>
              {numbers && <td className="ln">{l.oldNo ?? ""}</td>}
              {numbers && <td className="ln">{l.newNo ?? ""}</td>}
              <td className="code">
                <span className="sign">{l.type === "add" ? "+" : l.type === "del" ? "−" : " "}</span>
                {l.text}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {shown.length < lines.length && (
        <button className="btn small ghost diff-more" onClick={() => setAll(true)}>
          Show all {lines.length} lines
        </button>
      )}
    </>
  );
}
