"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { LoadingState } from "@/components/brand/loader";
import { useOrganization } from "@/components/dashboard/org-context";
import { AnomalousOrb } from "@/components/ui/anomalous-orb";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import {
  readLastOpenedPodId,
  subscribeToLastOpenedPodId,
} from "@/lib/pods/last-opened-pod";
import {
  readOnboardingSkippedFirstPod,
  subscribeToOnboardingSkippedFirstPod,
  markOnboardingSkippedFirstPod,
} from "@/lib/pods/onboarding-skip";
import {
  useCreateOrganization,
  useMyOrganizationInvitations,
} from "@/lib/hooks/use-organizations";
import { useAccessiblePods } from "@/lib/hooks/use-pods";
import { useProfile, useUpdateProfile } from "@/lib/hooks/use-user";
import {
  useCreateAgentRuntime,
  useUpdatePodDefaultAgentRuntime,
} from "@/lib/hooks/use-agent-runtime";
import { RuntimeProfileScope } from "lemma-sdk";
import {
  OrganizationInvitationStatus,
  OrganizationJoinPolicy,
  type Organization,
  type Pod,
} from "@/lib/types";
import {
  FIRST_RUN_DELIGHT,
  buildRecipeConversationHref,
  getRecipeById,
} from "@/lib/recipes/recipes";

import { SetupChrome, SetupShell } from "./account-onboarding-chrome";
import {
  buildPromptFromIntent,
  inferFullName,
  podNameForAudience,
  personalWorkspaceName,
  setupStepsForAudience,
  splitName,
  startRecipesForAudience,
  teamLabelForKind,
  teamWorkspaceName,
  type Audience,
  type ConnectChoice,
  type SetupStep,
  type TeamKind,
} from "./account-onboarding-helpers";
import {
  AudienceStep,
  BootStep,
  ConnectStep,
  IdentityStep,
  InvitationsStep,
  StartStep,
  TeamStep,
} from "./account-onboarding-steps";

export function AccountOnboarding({
  children,
  requireFirstPod = true,
  preflightFallback,
}: {
  children: React.ReactNode;
  requireFirstPod?: boolean;
  preflightFallback?: React.ReactNode;
}) {
  const { data: profile, isLoading: isLoadingProfile } = useProfile();
  const {
    currentOrg,
    organizations,
    isLoading: isLoadingOrganizations,
    setCurrentOrg,
  } = useOrganization();
  const isProfileComplete = Boolean(profile?.first_name?.trim());
  const lastOpenedPodId = useSyncExternalStore(
    subscribeToLastOpenedPodId,
    readLastOpenedPodId,
    () => null,
  );
  const hasLastOpenedPod = requireFirstPod && Boolean(lastOpenedPodId);
  const skippedFirstPod = useSyncExternalStore(
    subscribeToOnboardingSkippedFirstPod,
    readOnboardingSkippedFirstPod,
    () => null,
  );
  const hasSkippedFirstPod = requireFirstPod && Boolean(skippedFirstPod);
  const { data: podsData, isLoading: isLoadingPods } = useAccessiblePods({
    enabled: requireFirstPod && !hasLastOpenedPod && !hasSkippedFirstPod,
  });
  const pods = podsData?.items || [];
  const { data: invitationsData, isLoading: isLoadingInvitations } =
    useMyOrganizationInvitations(OrganizationInvitationStatus.PENDING, {
      enabled: isProfileComplete,
    });
  const pendingInvitations = invitationsData?.items || [];
  const needsProfile = Boolean(profile) && !isProfileComplete;
  const needsInvitations = isProfileComplete && pendingInvitations.length > 0;
  const needsOrganization =
    isProfileComplete && !isLoadingOrganizations && organizations.length === 0;
  const needsFirstPod =
    requireFirstPod &&
    !hasLastOpenedPod &&
    !hasSkippedFirstPod &&
    isProfileComplete &&
    !isLoadingPods &&
    pendingInvitations.length === 0 &&
    pods.length === 0;
  const [setupActive, setSetupActive] = useState(false);
  const nextSetupStep: SetupStep = needsProfile
    ? "identity"
    : needsOrganization || needsFirstPod
      ? "audience"
      : "audience";
  const setupInitialStep: SetupStep =
    setupActive || needsFirstPod ? nextSetupStep : "boot";

  if (
    !setupActive &&
    (isLoadingProfile ||
      isLoadingOrganizations ||
      (isProfileComplete && requireFirstPod && !hasLastOpenedPod && isLoadingPods) ||
      (isProfileComplete && isLoadingInvitations))
  ) {
    if (preflightFallback) {
      return preflightFallback;
    }

    return (
      <SetupShell>
        <LoadingState
          title="Preparing your workspace"
          description="Checking identity, workspace, invitations, and pods."
          shape="lines"
          className="w-full max-w-xl"
        />
      </SetupShell>
    );
  }

  if (needsInvitations) {
    return <InvitationsStep invitations={pendingInvitations} />;
  }

  if (needsProfile || needsOrganization || needsFirstPod || setupActive) {
    return (
      <SetupAssistant
        profile={profile}
        initialOrganization={currentOrg || organizations[0] || null}
        initialAudience={organizations.length > 0 ? "team" : null}
        startStep={nextSetupStep}
        initialStep={setupInitialStep}
        onSetupStart={() => setSetupActive(true)}
        onOrganizationReady={setCurrentOrg}
      />
    );
  }

  return <>{children}</>;
}

function SetupAssistant({
  profile,
  initialOrganization,
  initialAudience,
  startStep,
  initialStep,
  onSetupStart,
  onOrganizationReady,
}: {
  profile?: {
    email?: string | null;
    first_name?: string | null;
    last_name?: string | null;
    full_name?: string | null;
  } | null;
  initialOrganization: Organization | null;
  initialAudience: Audience | null;
  startStep: SetupStep;
  initialStep: SetupStep;
  onSetupStart: () => void;
  onOrganizationReady: (organization: Organization) => void;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const updateProfile = useUpdateProfile();
  const createOrganization = useCreateOrganization();
  const createAgentRuntime = useCreateAgentRuntime();
  const updatePodDefaultRuntime = useUpdatePodDefaultAgentRuntime();
  const email = profile?.email || "";
  const inferredName = inferFullName(profile);
  const [step, setStep] = useState<SetupStep>(initialStep);
  const [createdOrganization, setCreatedOrganization] =
    useState<Organization | null>(null);
  const [basePod, setBasePod] = useState<Pod | null>(null);
  const [isCreatingPod, setIsCreatingPod] = useState(false);
  const [isConnectingAi, setIsConnectingAi] = useState(false);
  const [connectedProfileId, setConnectedProfileId] = useState<string | null>(
    null,
  );
  const [identityName, setIdentityName] = useState(inferredName);
  const [audience, setAudience] = useState<Audience | null>(initialAudience);
  const [teamKind, setTeamKind] = useState<TeamKind | null>("support");
  const [customTeamName, setCustomTeamName] = useState("");
  const startRecipes = useMemo(
    () => startRecipesForAudience(audience ?? "personal"),
    [audience],
  );
  const [selectedRecipeId, setSelectedRecipeId] = useState(
    () => startRecipesForAudience(initialAudience ?? "personal")[0]?.id ?? "",
  );
  const [customIntent, setCustomIntent] = useState("");
  const activeOrganization = createdOrganization || initialOrganization;

  useEffect(() => {
    if (step === "boot" && initialStep !== "boot") {
      setStep(initialStep);
    }
  }, [initialStep, step]);

  const goTo = (nextStep: SetupStep) => {
    onSetupStart();
    setStep(nextStep);
  };

  const handleBegin = () => {
    goTo(startStep);
  };

  const handleIdentitySubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const parsed = splitName(identityName);
    if (!parsed.firstName) return;

    updateProfile.mutate(
      {
        first_name: parsed.firstName,
        last_name: parsed.lastName || null,
      },
      {
        onSuccess: () => {
          toast.success("Operator profile saved");
          goTo("audience");
        },
        onError: (error) =>
          toast.error(`Failed to save profile: ${error.message}`),
      },
    );
  };

  const resolveTeamName = (kind = teamKind, customName = customTeamName) =>
    teamLabelForKind(kind, customName);

  const ensureOrganization = async (
    audienceForPod: Audience,
    teamName = "",
  ): Promise<Organization | null> => {
    if (activeOrganization) return activeOrganization;

    const organization = await createOrganization.mutateAsync({
      name:
        audienceForPod === "personal"
          ? personalWorkspaceName(identityName)
          : teamWorkspaceName(teamName),
      join_policy: OrganizationJoinPolicy.INVITE_ONLY,
      email_domain: null,
    });
    setCreatedOrganization(organization);
    onOrganizationReady(organization);
    return organization;
  };

  const createBasePod = async (
    audienceForPod: Audience,
    teamName = "",
  ): Promise<Pod | null> => {
    setIsCreatingPod(true);
    try {
      const organization = await ensureOrganization(audienceForPod, teamName);
      if (!organization) {
        toast.error("Could not prepare your workspace");
        return null;
      }

      const podName = podNameForAudience(audienceForPod, teamName);
      const pod = await getLemmaClient().pods.create({
        name: podName,
        description:
          audienceForPod === "personal"
            ? "Personal pod created during onboarding. Add recipes, agents, apps, and automations here."
            : `${teamName || "Team"} pod created during onboarding. Add recipes, agents, apps, and automations here.`,
        organization_id: organization.id,
      });
      setBasePod(pod);
      queryClient.invalidateQueries({ queryKey: ["pods"] });
      toast.success(`${pod.name} created`);
      return pod;
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : "Failed to create pod";
      toast.error(message);
      return null;
    } finally {
      setIsCreatingPod(false);
    }
  };

  const handleAudienceSelect = async (value: Audience) => {
    setAudience(value);
    setCustomIntent("");
    setSelectedRecipeId(startRecipesForAudience(value)[0]?.id ?? "");

    if (value === "team") {
      goTo("team");
      return;
    }

    const pod = await createBasePod("personal");
    if (pod) goTo("connect");
  };

  const handleTeamContinue = async () => {
    const teamName = resolveTeamName();
    if (!teamName.trim()) {
      toast.error("Choose or name a team first");
      return;
    }

    const pod = await createBasePod("team", teamName);
    if (pod) goTo("connect");
  };

  const handleConnectContinue = async (choice: ConnectChoice) => {
    if (choice.kind === "lemma") {
      goTo("start");
      return;
    }

    setIsConnectingAi(true);
    try {
      const teamName = resolveTeamName();
      const organization = await ensureOrganization(audience ?? "personal", teamName);
      if (!organization) {
        toast.error("Could not prepare your workspace");
        return;
      }

      let runtimeProfileId: string | null = null;
      if (choice.kind === "daemon") {
        const profile = await createAgentRuntime.mutateAsync({
          organizationId: organization.id,
          request: {
            source: "USER_DAEMON",
            daemon_id: choice.daemonId,
            harness_kind: choice.harnessKind,
            scope: RuntimeProfileScope.PERSONAL,
            name: `${choice.displayName} daemon`,
            default_model_name: choice.modelName || undefined,
          },
        });
        runtimeProfileId = profile.id;
        setConnectedProfileId(profile.id);
        toast.success(`${choice.displayName} connected`);
      } else {
        const profile = await createAgentRuntime.mutateAsync({
          organizationId: organization.id,
          request:
            choice.providerKind === "openai"
              ? {
                  source: "OPENAI_COMPATIBLE",
                  name: choice.name,
                  base_url: choice.baseUrl,
                  api_key: choice.apiKey || null,
                  default_model_name: choice.defaultModelName,
                  model_names: choice.modelNames,
                }
              : {
                  source: "ANTHROPIC_COMPATIBLE",
                  name: choice.name,
                  base_url: choice.baseUrl || null,
                  api_key: choice.apiKey,
                  default_model_name: choice.defaultModelName,
                  model_names: choice.modelNames,
                },
        });
        runtimeProfileId = profile.id;
        setConnectedProfileId(profile.id);
        toast.success(`${choice.name} saved`);
      }

      if (basePod && runtimeProfileId) {
        await updatePodDefaultRuntime.mutateAsync({
          podId: basePod.id,
          runtime: { profile_id: runtimeProfileId, model_name: null },
        });
      }
      goTo("start");
    } catch (error) {
      const message =
        error instanceof Error && error.message
          ? error.message
          : "Failed to connect AI";
      toast.error(message);
    } finally {
      setIsConnectingAi(false);
    }
  };

  const handleSkipFirstPod = () => {
    if (basePod) {
      router.push(`/pod/${basePod.id}`);
      return;
    }

    markOnboardingSkippedFirstPod();
    router.replace("/home");
  };

  const requireBasePod = () => {
    if (basePod) return basePod;
    toast.error("Create a pod first");
    goTo(audience === "team" ? "team" : "audience");
    return null;
  };

  const openBuildConversation = (pod: Pod, message: string, metadataIntent: string) => {
    const params = new URLSearchParams({
      assistantMessage: message,
      conversationInstructions: [
        FIRST_RUN_DELIGHT,
        `The pod already exists: ${pod.name}. Do not create another pod. Use the user-visible message as the goal and build inside the current pod. Inspect existing resources first, reuse anything that fits, seed believable sample data, and wire any surface or connector that fits how they already work.`,
      ].join("\n\n"),
      conversationMetadata: JSON.stringify({
        source: "onboarding",
        intent: metadataIntent,
        first_run: true,
        pod_id: pod.id,
      }),
    });
    router.push(`/pod/${pod.id}/conversations/new?${params.toString()}`);
  };

  const handleBuildWithLemma = () => {
    const pod = requireBasePod();
    if (!pod) return;

    openBuildConversation(
      pod,
      `Help me build the first useful capability inside ${pod.name}. If you need context, ask one short question, then make a working first version.`,
      "build_with_lemma",
    );
  };

  const handleCreateFromStart = () => {
    const pod = requireBasePod();
    if (!pod) return;

    // A typed brief always wins over a preselected card.
    const intentText = customIntent.trim();
    const recipe = intentText ? null : getRecipeById(selectedRecipeId);
    if (!intentText && !recipe) {
      toast.error("Describe what you want, or pick a starting point");
      return;
    }

    if (recipe) {
      router.push(
        buildRecipeConversationHref(pod.id, recipe, {
          podName: pod.name,
          mode: recipe.source.kind === "repo" ? "install" : undefined,
          firstRun: true,
        }),
      );
      return;
    }

    if (connectedProfileId) {
      void updatePodDefaultRuntime.mutateAsync({
        podId: pod.id,
        runtime: { profile_id: connectedProfileId, model_name: null },
      });
    }

    if (intentText) {
      const params = new URLSearchParams({
        assistantMessage: buildPromptFromIntent(intentText),
        conversationInstructions: [
          FIRST_RUN_DELIGHT,
          `The pod already exists: ${pod.name}. Do not create another pod. Use the user-visible message as the goal and build the smallest useful first version inside the current pod. Seed believable sample data and wire any surface or connector that fits how they already work.`,
        ].join("\n\n"),
        conversationMetadata: JSON.stringify({
          source: "onboarding",
          intent: "build_inside_existing_pod",
          first_run: true,
          pod_id: pod.id,
        }),
      });
      router.push(`/pod/${pod.id}/conversations/new?${params.toString()}`);
    }
  };

  if (step === "boot") {
    return (
      <SetupShell fullBleed>
        <div className="relative flex min-h-screen w-full flex-col overflow-hidden">
          <div className="setup-card-glow absolute inset-0" />
          {/* Country-skyline morph is disabled for now — revisit once the
              transition into the split-view steps is settled. */}
          {/* <IntroSkylines /> */}
          <div className="relative z-10 flex flex-1 flex-col px-5 py-5 sm:px-7 sm:py-6">
            <SetupChrome />
            <div className="mx-auto flex flex-1 max-w-4xl flex-col items-center justify-center pb-16">
              <AnomalousOrb className="static mb-8 h-40 w-40 shrink-0 sm:h-48 sm:w-48" />
              <BootStep onBegin={handleBegin} />
            </div>
          </div>
        </div>
      </SetupShell>
    );
  }

  const orderedSteps = setupStepsForAudience(audience);
  const handleBack = () => {
    const currentIndex = orderedSteps.indexOf(step);
    if (currentIndex <= 0) return;
    goTo(orderedSteps[currentIndex - 1]);
  };

  return (
    <SetupShell fullBleed>
      {step === "identity" ? (
        <IdentityStep
          email={email}
          name={identityName}
          isSaving={updateProfile.isPending}
          onNameChange={setIdentityName}
          onSubmit={handleIdentitySubmit}
          onBack={handleBack}
          steps={orderedSteps}
        />
      ) : step === "audience" ? (
        <AudienceStep
          audience={audience}
          isSaving={isCreatingPod}
          savingAudience="personal"
          onSelect={handleAudienceSelect}
          onBack={handleBack}
          steps={orderedSteps}
        />
      ) : step === "team" ? (
        <TeamStep
          teamKind={teamKind}
          customTeamName={customTeamName}
          isCreating={isCreatingPod}
          onTeamKindChange={setTeamKind}
          onCustomTeamNameChange={setCustomTeamName}
          onContinue={handleTeamContinue}
          onBack={handleBack}
          steps={orderedSteps}
        />
      ) : step === "connect" ? (
        <ConnectStep
          isSaving={isConnectingAi}
          onContinue={handleConnectContinue}
          onBack={handleBack}
          steps={orderedSteps}
        />
      ) : (
        <StartStep
          audience={audience ?? "personal"}
          podName={basePod?.name ?? podNameForAudience(audience ?? "personal", resolveTeamName())}
          recipes={startRecipes}
          selectedRecipeId={selectedRecipeId}
          customIntent={customIntent}
          isCreating={false}
          onSelectRecipe={(id) => {
            setCustomIntent("");
            setSelectedRecipeId(id);
          }}
          onCustomIntentChange={(value) => {
            setCustomIntent(value);
            // Typing overrides a card; clearing restores the default pick.
            setSelectedRecipeId(value.trim() ? "" : startRecipes[0]?.id ?? "");
          }}
          onBuildWithLemma={handleBuildWithLemma}
          onContinue={handleCreateFromStart}
          onSkip={handleSkipFirstPod}
          onBack={handleBack}
          steps={orderedSteps}
        />
      )}
    </SetupShell>
  );
}
