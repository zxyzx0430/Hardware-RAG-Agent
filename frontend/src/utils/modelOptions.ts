/** Model dropdown grouping: dedupe repeated routers/providers and support search. */

export interface ModelProviderLike {
  id: string;
  name: string;
  models: string[];
}

export interface ModelOptionGroup {
  providerId: string;
  providerName: string;
  models: string[];
}

/**
 * Build provider groups with each model id appearing once.
 *
 * Several local routers can expose the same model list. The selected
 * provider wins the duplicate so the current selection stays visible; other
 * duplicates fall back to the first provider that offers the model.
 * Selecting an option still writes that provider id + model id, so the
 * provider/model selection semantics are unchanged.
 */
export function buildModelGroups(
  providers: ModelProviderLike[],
  selectedProviderId: string,
): ModelOptionGroup[] {
  const chosenProvider = new Map<string, string>();

  const selected = providers.find((provider) => provider.id === selectedProviderId);
  if (selected) {
    for (const modelId of selected.models) {
      if (modelId && !chosenProvider.has(modelId)) chosenProvider.set(modelId, selected.id);
    }
  }
  for (const provider of providers) {
    for (const modelId of provider.models) {
      if (modelId && !chosenProvider.has(modelId)) chosenProvider.set(modelId, provider.id);
    }
  }

  return providers
    .map((provider) => ({
      providerId: provider.id,
      providerName: provider.name,
      models: Array.from(new Set(provider.models)).filter(
        (modelId) => chosenProvider.get(modelId) === provider.id,
      ),
    }))
    .filter((group) => group.models.length > 0);
}

/** Filter model rows by model id or provider name. A matching provider keeps all its models. */
export function filterModelGroups(groups: ModelOptionGroup[], query: string): ModelOptionGroup[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return groups;
  return groups.flatMap((group) => {
    if (group.providerName.toLowerCase().includes(needle)) return [group];
    const models = group.models.filter((modelId) => modelId.toLowerCase().includes(needle));
    return models.length > 0 ? [{ ...group, models }] : [];
  });
}
