import { KIND_LABEL, KINDS, type RuleKind } from "@/lib/api";

export function KindSelect({ id, value, onChange }: { id: string; value: RuleKind; onChange: (k: RuleKind) => void }) {
  return (
    <select id={id} value={value} onChange={(e) => onChange(e.target.value as RuleKind)}>
      {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
    </select>
  );
}
