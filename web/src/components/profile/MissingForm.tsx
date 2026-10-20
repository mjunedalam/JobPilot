import { useState, type FormEvent } from "react";
import type { Schemas } from "../../api/client";
import { useProfileAnswers } from "../../api/queries";
import { Button, ErrorNote, Field } from "../ui";

export function MissingForm({ missing }: { missing: Schemas["MissingFieldOut"][] }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const save = useProfileAnswers();
  const answered = Object.values(values).some((v) => v.trim());

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate(values, { onSuccess: () => setValues({}) });
  }

  return (
    <form onSubmit={submit} className="missing-form">
      {missing.map((m) => (
        <Field key={m.field} label={m.question}>
          <input
            className="input"
            name={m.field}
            value={values[m.field] ?? ""}
            onChange={(e) => setValues({ ...values, [m.field]: e.target.value })}
          />
        </Field>
      ))}
      <ErrorNote error={save.error} />
      <Button variant="primary" type="submit" busy={save.isPending} disabled={!answered}>
        Save answers
      </Button>
    </form>
  );
}
