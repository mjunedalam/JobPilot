import { FileUp } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent } from "react";
import {
  rememberedImport,
  rememberImport,
  useActiveImport,
  useResumeImportJob,
  useStartImport,
} from "../../api/queries";
import { Button, ErrorNote } from "../ui";
import { ImportProgress } from "./ImportProgress";

const ACCEPT = ".pdf,.docx";

export function ResumeImport() {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [over, setOver] = useState(false);
  const [jobId, setJobId] = useState<string | null>(rememberedImport);
  const start = useStartImport();
  const active = useActiveImport();
  const job = useResumeImportJob(jobId);

  // After a reload or a visit to another page, pick up an import that is still running.
  useEffect(() => {
    if (!jobId && active.data?.id) setJobId(active.data.id);
  }, [active.data, jobId]);

  // The server forgot the job (it restarted): back to the picker.
  useEffect(() => {
    if (job.isError) {
      rememberImport(null);
      setJobId(null);
    }
  }, [job.isError]);

  function pick(next: File | undefined) {
    if (!next) return;
    setFile(next);
    setLabel(next.name.replace(/\.(pdf|docx)$/i, ""));
    start.reset();
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    pick(event.dataTransfer.files[0]);
  }

  function begin() {
    if (!file) return;
    start.mutate({ file, label: label.trim() || undefined }, { onSuccess: (j) => setJobId(j.id) });
  }

  function again() {
    rememberImport(null);
    setJobId(null);
    setFile(null);
    start.reset();
  }

  if (jobId && job.data) return <ImportProgress job={job.data} onAgain={again} />;

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
        <input ref={input} type="file" accept={ACCEPT} hidden onChange={(e) => pick(e.target.files?.[0])} />
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
          <Button variant="primary" busy={start.isPending || Boolean(jobId)} onClick={begin}>
            Import
          </Button>
        </div>
      )}
      <p className="muted import-note">
        You'll watch it happen: your details appear as the model finds them. A local model takes
        about 1–2 minutes.
      </p>
      <ErrorNote error={start.error} />
    </div>
  );
}
