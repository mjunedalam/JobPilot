import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap, uploadResume, type Schemas } from "./client";

export type Role = Schemas["ModelRole"];
export type Provider = Schemas["ProviderOut"];
export type Route = Schemas["RouteOut"];
export type Preset = Schemas["PresetOut"];
export type Profile = Schemas["CandidateProfile"];

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

export function useImportResume() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ file, label }: { file: File; label?: string }) => uploadResume(file, label),
    onSuccess: () => invalidate(keys.profile, keys.versions, keys.missing),
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
