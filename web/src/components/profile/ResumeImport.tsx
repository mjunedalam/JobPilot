import { FileUp } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent } from "react";
import { useImportResume } from "../../api/queries";
import { Button, ErrorNote } from "../ui";

const ACCEPT = ".pdf,.docx";

function useElapsed(running: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!running) return;
    setSeconds(0);
    const started = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 500);
    return () => window.clearInterval(timer);
  }, [running]);
  return seconds;
}

export function ResumeImport() {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [over, setOver] = useState(false);
  const importer = useImportResume();
  const elapsed = useElapsed(importer.isPending);

  function pick(next: File | undefined) {
    if (!next) return;
    setFile(next);
    setLabel(next.name.replace(/\.(pdf|docx)$/i, ""));
    importer.reset();
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    pick(event.dataTransfer.files[0]);
  }

  return (
    <div className="import">
      <div
        className={`dropzone ${over ? "dropzone-over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        role="button"
        tabIndex={0}
        aria-label="Choose a resume file"
      >
        <FileUp size={22} />
        <span className="dropzone-title">{file ? file.name : "Drop your resume here"}</span>
        <span className="muted">PDF or DOCX, up to 10 MB</span>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => pick(e.target.files?.[0])}
        />
      </div>
      {file && (
        <div className="import-row">
          <input
            className="input"
            aria-label="Resume label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Label, e.g. backend"
          />
          <Button
            variant="primary"
            busy={importer.isPending}
            onClick={() => importer.mutate({ file, label: label.trim() || undefined })}
          >
            {importer.isPending ? `Reading… ${elapsed}s` : "Import"}
          </Button>
        </div>
      )}
      {importer.isPending && (
        <p className="muted import-note">
          Your extraction model is reading the resume. Local models can take a minute.
        </p>
      )}
      {importer.data && (
        <p className="trial trial-ok" role="status">
          {importer.data.version
            ? `Saved as profile version ${importer.data.version}.`
            : "No changes: the profile already had this information."}{" "}
          {importer.data.missing.length > 0 && `${importer.data.missing.length} details still needed below.`}
        </p>
      )}
      <ErrorNote error={importer.error} />
    </div>
  );
}
