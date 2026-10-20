import { useState, type FormEvent } from "react";
import { useImportJob, useJobs } from "../api/queries";
import { JobCard } from "../components/jobs/JobCard";
import { ReportStrip } from "../components/jobs/ReportStrip";
import { SearchPanel } from "../components/jobs/SearchPanel";
import { Button, Empty, ErrorNote, Panel } from "../components/ui";

export function Jobs() {
  const jobs = useJobs();
  const importer = useImportJob();
  const [url, setUrl] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    importer.mutate(url.trim(), { onSuccess: () => setUrl("") });
  }

  const list = jobs.data ?? [];
  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Cockpit</p>
        <h1 className="page-title">Jobs</h1>
        <p className="page-lede">
          Recent postings from your portals and any careers page, merged across sources and ranked
          against your profile. Approving and applying arrive in Phase 3.
        </p>
      </header>

      <div className="jobs-top">
        <SearchPanel />
        <Panel eyebrow="Import" title="Add one page">
          <form onSubmit={submit} className="stack">
            <p className="muted">Paste a job posting or a company careers page. The extraction model reads it, and only links that exist on the page are kept.</p>
            <input className="input" type="url" placeholder="https://company.com/careers/backend-engineer"
                   value={url} onChange={(e) => setUrl(e.target.value)} required />
            <Button variant="primary" type="submit" busy={importer.isPending}>Import</Button>
            <ErrorNote error={importer.error} />
          </form>
          {importer.data && <ReportStrip report={importer.data} />}
        </Panel>
      </div>

      <section className="section" aria-labelledby="ranked-title">
        <div className="section-head">
          <h2 id="ranked-title" className="section-title">Ranked for you</h2>
          <span className="muted">{list.length} waiting for review</span>
        </div>
        <ErrorNote error={jobs.error} />
        {list.length === 0 && !jobs.isLoading && (
          <Empty title="No jobs yet">Run a search or import a page to fill this list.</Empty>
        )}
        <div className="job-list">
          {list.map((job, i) => <JobCard key={job.id} job={job} rank={i + 1} />)}
        </div>
      </section>
    </div>
  );
}
