import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getToken, unwrap, uploadResume, type Schemas } from "./client";

export type Role = Schemas["ModelRole"];
export type Provider = Schemas["ProviderOut"];
export type Route = Schemas["RouteOut"];
export type Preset = Schemas["PresetOut"];
export type Profile = Schemas["CandidateProfile"];
export type ImportResult = Schemas["ImportResultOut"];

export const keys = {
  health: ["health"] as const,
  profile: ["profile"] as const,
  versions: ["profile", "versions"] as const,
  missing: ["profile", "missing"] as const,
  answers: ["answers"] as const,
  providers: ["llm", "providers"] as const,
  routes: ["llm", "routes"] as const,
  presets: ["llm", "presets"] as const,
  llmSettings: ["llm", "settings"] as const,
  models: (provider: string) => ["llm", "models", provider] as const,
};

export const useHealth = () =>
  useQuery({
    queryKey: keys.health,
    queryFn: async () => unwrap(await api.GET("/api/v1/health")),
    refetchInterval: 15_000,
  });

export const useProfile = () =>
  useQuery({ queryKey: keys.profile, queryFn: async () => unwrap(await api.GET("/api/v1/profile")) });

export const useVersions = () =>
  useQuery({
    queryKey: keys.versions,
    queryFn: async () => unwrap(await api.GET("/api/v1/profile/versions")),
  });

export const useMissing = () =>
  useQuery({
    queryKey: keys.missing,
    queryFn: async () => unwrap(await api.GET("/api/v1/profile/missing")),
  });

export const useAnswers = () =>
  useQuery({ queryKey: keys.answers, queryFn: async () => unwrap(await api.GET("/api/v1/answers")) });

export const useProviders = () =>
  useQuery({
    queryKey: keys.providers,
    queryFn: async () => unwrap(await api.GET("/api/v1/llm/providers")),
  });

export const useRoutes = () =>
  useQuery({ queryKey: keys.routes, queryFn: async () => unwrap(await api.GET("/api/v1/llm/routes")) });

export const usePresets = () =>
  useQuery({
    queryKey: keys.presets,
    queryFn: async () => unwrap(await api.GET("/api/v1/llm/presets")),
    staleTime: Infinity,
  });

export const useLLMSettings = () =>
  useQuery({
    queryKey: keys.llmSettings,
    queryFn: async () => unwrap(await api.GET("/api/v1/llm/settings")),
  });

/** Live model list from the provider itself (Ollama tags, OpenAI /models, Anthropic models). */
export const useProviderModels = (provider: string | undefined) =>
  useQuery({
    queryKey: keys.models(provider ?? ""),
    enabled: Boolean(provider),
    retry: false,
    staleTime: 60_000,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/llm/providers/{name}/models", {
          params: { path: { name: provider as string } },
        }),
      ).models,
  });

function useInvalidate() {
  const client = useQueryClient();
  return (...queryKeys: readonly (readonly unknown[])[]) =>
    Promise.all(queryKeys.map((queryKey) => client.invalidateQueries({ queryKey })));
}

/** Free-text answers parsed server-side into typed profile fields; saved as a new version. */
export function useProfileAnswers() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (answers: Record<string, string>) =>
      unwrap(await api.POST("/api/v1/profile/qa", { body: { answers } })),
    onSuccess: () => invalidate(keys.profile, keys.versions, keys.missing),
  });
}

export type ImportJob = Schemas["ImportJobOut"];
const IMPORT_KEY = "jobpilot.importJob";

/** Start reading a resume. Returns at once with a job to follow (see useImportJob). */
export function useStartImport() {
  return useMutation({
    mutationFn: ({ file, label }: { file: File; label?: string }) => uploadResume(file, label),
    onSuccess: (job) => rememberImport(job.id),
  });
}

export function rememberImport(id: string | null) {
  try {
    if (id) sessionStorage.setItem(IMPORT_KEY, id);
    else sessionStorage.removeItem(IMPORT_KEY);
  } catch {
    /* storage unavailable: the job is still found through /import/active */
  }
}

export function rememberedImport(): string | null {
  try {
    return sessionStorage.getItem(IMPORT_KEY);
  } catch {
    return null;
  }
}

/** The import still running on the server, so a reload picks the live view up again. */
export const useActiveImport = () =>
  useQuery({
    queryKey: ["profile", "import", "active"],
    queryFn: async () => unwrap(await api.GET("/api/v1/profile/import/active")),
    staleTime: Infinity,
  });

/** Live snapshot of one resume import: polled twice a second while it runs. */
export function useResumeImportJob(id: string | null) {
  const invalidate = useInvalidate();
  return useQuery({
    queryKey: ["profile", "import", id],
    enabled: Boolean(id),
    retry: false,
    queryFn: async () => {
      const job = unwrap(await api.GET("/api/v1/profile/import/{job_id}", {
        params: { path: { job_id: id as string } },
      }));
      if (job.status === "done") void invalidate(keys.profile, keys.versions, keys.missing);
      return job;
    },
    refetchInterval: (q) => (q.state.data && q.state.data.status !== "running" ? false : 500),
  });
}

export function useAddAnswer() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (body: Schemas["AnswerIn"]) =>
      unwrap(await api.POST("/api/v1/answers", { body })),
    onSuccess: () => invalidate(keys.answers),
  });
}

export function useSaveRoute() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ role, body }: { role: Role; body: Schemas["RouteIn"] }) =>
      unwrap(await api.PUT("/api/v1/llm/routes/{role}", { params: { path: { role } }, body })),
    onSuccess: () => invalidate(keys.routes, keys.llmSettings),
  });
}

export function useResetRoute() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (role: Role) =>
      unwrap(await api.DELETE("/api/v1/llm/routes/{role}", { params: { path: { role } } })),
    onSuccess: () => invalidate(keys.routes, keys.llmSettings),
  });
}

export function useTryRole() {
  return useMutation({
    mutationFn: async (role: Role) =>
      unwrap(await api.POST("/api/v1/llm/routes/{role}/try", { params: { path: { role } } })),
  });
}

export function useSaveProvider() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ name, body }: { name: string; body: Schemas["ProviderIn"] }) =>
      unwrap(await api.PUT("/api/v1/llm/providers/{name}", { params: { path: { name } }, body })),
    onSuccess: (_data, { name }) => invalidate(keys.providers, keys.models(name), keys.llmSettings),
  });
}

export function useDeleteProvider() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (name: string) =>
      unwrap(await api.DELETE("/api/v1/llm/providers/{name}", { params: { path: { name } } })),
    onSuccess: () => invalidate(keys.providers, keys.routes),
  });
}

export function useTestProvider() {
  return useMutation({
    mutationFn: async (name: string) =>
      unwrap(await api.POST("/api/v1/llm/providers/{name}/test", { params: { path: { name } } })),
  });
}

export function useSetPrivacy() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (privacy_mode: Schemas["PrivacyMode"]) =>
      unwrap(await api.PUT("/api/v1/llm/settings/privacy", { body: { privacy_mode } })),
    onSuccess: () => invalidate(keys.llmSettings, keys.health),
  });
}

export function useKillSwitch() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (engage: boolean) =>
      engage
        ? unwrap(await api.POST("/api/v1/system/stop", { body: { reason: "stopped from the UI" } }))
        : unwrap(await api.POST("/api/v1/system/resume")),
    onSuccess: () => invalidate(keys.health),
  });
}

// ---- Jobs, search and portals (Phase 2) ----------------------------------------------

export type Job = Schemas["JobOut"];
export type SearchReport = Schemas["SearchReportOut"];
export type Portal = Schemas["PortalOut"];
export type RecencyPreset = Schemas["RecencyPreset"];

export const jobKeys = {
  jobs: ["jobs"] as const,
  job: (id: number) => ["jobs", id] as const,
  portals: ["portals"] as const,
};

export const useJobs = () =>
  useQuery({ queryKey: jobKeys.jobs, queryFn: async () => unwrap(await api.GET("/api/v1/jobs")) });

export const useJob = (id: number | null) =>
  useQuery({
    queryKey: jobKeys.job(id ?? 0),
    enabled: id !== null,
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: id as number } } })),
  });

export const usePortals = () =>
  useQuery({ queryKey: jobKeys.portals, queryFn: async () => unwrap(await api.GET("/api/v1/portals")) });

export function useRunSearch() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (body: Schemas["SearchIn"]) => unwrap(await api.POST("/api/v1/search", { body })),
    onSuccess: () => invalidate(jobKeys.jobs, jobKeys.portals),
  });
}

export function useImportJob() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (url: string) => unwrap(await api.POST("/api/v1/jobs/import", { body: { url } })),
    onSuccess: () => invalidate(jobKeys.jobs),
  });
}

type PortalName = "naukri" | "linkedin" | "shine";

export function useSetPortalEnabled() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ name, enabled, accept_terms }: { name: PortalName; enabled: boolean; accept_terms: boolean }) =>
      unwrap(await api.PUT("/api/v1/portals/{name}/enabled", {
        params: { path: { name } },
        body: { enabled, accept_terms },
      })),
    onSuccess: () => invalidate(jobKeys.portals),
  });
}

export function useSavePortalCredentials() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ name, username, password }: { name: PortalName; username: string; password: string }) =>
      unwrap(await api.PUT("/api/v1/portals/{name}/credentials", {
        params: { path: { name } },
        body: { username, password },
      })),
    onSuccess: () => invalidate(jobKeys.portals),
  });
}

export function usePortalLogin() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ name, method }: { name: PortalName; method: "manual" | "password" }) =>
      unwrap(await api.POST("/api/v1/portals/{name}/login", { params: { path: { name } }, body: { method } })),
    onSettled: () => invalidate(jobKeys.portals),
  });
}

export function useDisconnectPortal() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (name: PortalName) =>
      unwrap(await api.DELETE("/api/v1/portals/{name}/session", { params: { path: { name } } })),
    onSuccess: () => invalidate(jobKeys.portals),
  });
}

// ---- Workflows and decisions (Phase 3) -----------------------------------------------

export type Decision = Schemas["DecisionOut"];
export type WorkflowRun = Schemas["WorkflowOut"];
export type ApplicationRow = Schemas["ApplicationOut"];

export const flowKeys = {
  decisions: ["decisions"] as const,
  workflows: ["workflows"] as const,
  applications: ["applications"] as const,
};

/** Poll while a run is working so the next decision appears without a refresh. */
export const useWorkflows = () =>
  useQuery({
    queryKey: flowKeys.workflows,
    queryFn: async () => unwrap(await api.GET("/api/v1/workflows")),
    refetchInterval: (q) =>
      (q.state.data ?? []).some((r) => r.status === "running") ? 2000 : 15_000,
  });

export const useDecisions = (fast: boolean) =>
  useQuery({
    queryKey: flowKeys.decisions,
    queryFn: async () => unwrap(await api.GET("/api/v1/decisions")),
    refetchInterval: fast ? 2000 : 15_000,
  });

/** Poll while a run is working: applications change in the background, not on a click. */
export const useApplications = (fast = false) =>
  useQuery({
    queryKey: flowKeys.applications,
    queryFn: async () => unwrap(await api.GET("/api/v1/applications")),
    refetchInterval: fast ? 2000 : false,
  });

export function useStartApply() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/workflows/apply")),
    onSuccess: () => invalidate(flowKeys.workflows, flowKeys.decisions),
  });
}

export function useAnswerDecision() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ decision, answer }: { decision: Decision; answer: Record<string, unknown> }) =>
      unwrap(await api.POST("/api/v1/decisions/{thread_id}", {
        params: { path: { thread_id: decision.thread_id } },
        body: { interrupt_id: decision.interrupt_id, answer },
      })),
    onSettled: () => invalidate(flowKeys.decisions, flowKeys.workflows, flowKeys.applications, jobKeys.jobs),
  });
}

/** Proof screenshots need the bearer token, so they are fetched and shown as a blob URL. */
export async function fetchProof(applicationId: number): Promise<string> {
  const token = getToken();
  const response = await fetch(`/api/v1/applications/${applicationId}/proof`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw new Error(`proof unavailable (${response.status})`);
  return URL.createObjectURL(await response.blob());
}

// ---- Mail, emails and replies (Phase 4) ----------------------------------------------

export type MailStatus = Schemas["MailStatusOut"];
export type EmailRow = Schemas["EmailOut"];
export type EmailDetail = Schemas["EmailDetailOut"];
export type ResumeRow = Schemas["ResumeOut"];
export type PollReport = Schemas["PollOut"];

export const mailKeys = {
  status: ["mail"] as const,
  emails: (category: string) => ["emails", category] as const,
  allEmails: ["emails"] as const,
  email: (id: number) => ["emails", "detail", id] as const,
  resumes: ["resumes"] as const,
};

export const useMailStatus = () =>
  useQuery({
    queryKey: mailKeys.status,
    queryFn: async () => unwrap(await api.GET("/api/v1/mail")),
    refetchInterval: 30_000,
  });

export const useEmails = (category: string) =>
  useQuery({
    queryKey: mailKeys.emails(category),
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/emails", {
        params: { query: category === "all" ? {} : category === "action" ? { action_status: "needs_action" } : { category } },
      })),
    refetchInterval: 30_000,
  });

export const useResumes = () =>
  useQuery({ queryKey: mailKeys.resumes, queryFn: async () => unwrap(await api.GET("/api/v1/resumes")) });

export function useConnectMail() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (body: Schemas["ConnectIn"]) => unwrap(await api.POST("/api/v1/mail/connect", { body })),
    onSuccess: (data) => {
      // Gmail / Outlook: consent happens on the provider's page, in a new tab.
      if (data.authorization_url) window.open(data.authorization_url, "_blank", "noopener");
      invalidate(mailKeys.status);
    },
  });
}

export function useDisconnectMail() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async () => unwrap(await api.DELETE("/api/v1/mail")),
    onSuccess: () => invalidate(mailKeys.status),
  });
}

export function usePollMail() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/mail/poll")),
    onSettled: () => invalidate(mailKeys.status, mailKeys.allEmails, flowKeys.decisions, flowKeys.workflows, flowKeys.applications),
  });
}

/** An unverified email's suggested status, applied as the user's own (audited) decision. */
export function useApplySuggestion() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (emailId: number) =>
      unwrap(await api.POST("/api/v1/emails/{email_id}/apply-suggestion", {
        params: { path: { email_id: emailId } },
      })),
    onSettled: () => invalidate(mailKeys.allEmails, flowKeys.applications),
  });
}

/** Start a new reply (H6 → H7) for an email, e.g. after a refused send. */
export function useReplyAgain() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (emailId: number) =>
      unwrap(await api.POST("/api/v1/emails/{email_id}/reply", { params: { path: { email_id: emailId } } })),
    onSettled: () => invalidate(mailKeys.allEmails, flowKeys.decisions, flowKeys.workflows),
  });
}
