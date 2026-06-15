import type { AnalysisKind } from "@/lib/api";

const RUN_TEMPLATES_STORAGE_KEY = "andes.runTemplates.v1";
export const MAX_TEMPLATE_TEXT_CHARS = 100_000;

export type GoNamespace = "biological_process" | "molecular_function" | "cellular_component";

export interface GeneSetCollectionTemplate {
  mode: "default" | "gmt" | "go";
  gmtFileName?: string | null;
  oboFileName?: string | null;
  annotationFileName?: string | null;
}

export interface SetSimilarityTemplateFields {
  genes: string;
  genesTextOmitted?: boolean;
  genesFileName?: string | null;
  minSize: number;
  maxSize: number;
  goNamespace: GoNamespace;
  queryCollection: GeneSetCollectionTemplate;
  targetCollection: GeneSetCollectionTemplate;
}

export interface GseaTemplateFields {
  ranked: string;
  rankedTextOmitted?: boolean;
  rankedFileName?: string | null;
  minSize: number;
  maxSize: number;
  goNamespace: GoNamespace;
  geneSetCollection: GeneSetCollectionTemplate;
}

export interface RunTemplate<TFields extends object = object> {
  id: string;
  kind: AnalysisKind;
  name: string;
  fields: TFields;
  created_at: string;
  updated_at: string;
}

function isAnalysisKind(value: unknown): value is AnalysisKind {
  return value === "set_similarity" || value === "gsea";
}

function isGoNamespace(value: unknown): value is GoNamespace {
  return (
    value === "biological_process" ||
    value === "molecular_function" ||
    value === "cellular_component"
  );
}

function optionalString(value: unknown): string | null | undefined {
  if (value === null) return null;
  return typeof value === "string" ? value : undefined;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function coerceCollection(value: unknown): GeneSetCollectionTemplate | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (record.mode === "default") {
    return { mode: "default" };
  }
  if (record.mode === "gmt") {
    const gmtFileName = optionalString(record.gmtFileName);
    return gmtFileName !== undefined ? { mode: "gmt", gmtFileName } : null;
  }
  if (record.mode === "go") {
    const oboFileName = optionalString(record.oboFileName);
    const annotationFileName = optionalString(record.annotationFileName);
    if (oboFileName === undefined || annotationFileName === undefined) return null;
    return { mode: "go", oboFileName, annotationFileName };
  }
  return null;
}

function coerceSetSimilarityFields(value: unknown): SetSimilarityTemplateFields | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const genesFileName = optionalString(record.genesFileName);
  const queryCollection = coerceCollection(record.queryCollection);
  const targetCollection = coerceCollection(record.targetCollection);
  if (
    typeof record.genes !== "string" ||
    genesFileName === undefined ||
    !isFiniteNumber(record.minSize) ||
    !isFiniteNumber(record.maxSize) ||
    !isGoNamespace(record.goNamespace) ||
    queryCollection === null ||
    targetCollection === null
  ) {
    return null;
  }
  return {
    genes: record.genes,
    genesTextOmitted: record.genesTextOmitted === true,
    genesFileName,
    minSize: record.minSize,
    maxSize: record.maxSize,
    goNamespace: record.goNamespace,
    queryCollection,
    targetCollection
  };
}

function coerceGseaFields(value: unknown): GseaTemplateFields | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const rankedFileName = optionalString(record.rankedFileName);
  const geneSetCollection = coerceCollection(record.geneSetCollection);
  if (
    typeof record.ranked !== "string" ||
    rankedFileName === undefined ||
    !isFiniteNumber(record.minSize) ||
    !isFiniteNumber(record.maxSize) ||
    !isGoNamespace(record.goNamespace) ||
    geneSetCollection === null
  ) {
    return null;
  }
  return {
    ranked: record.ranked,
    rankedTextOmitted: record.rankedTextOmitted === true,
    rankedFileName,
    minSize: record.minSize,
    maxSize: record.maxSize,
    goNamespace: record.goNamespace,
    geneSetCollection
  };
}

function coerceFields(kind: AnalysisKind, value: unknown): object | null {
  if (kind === "set_similarity") return coerceSetSimilarityFields(value);
  return coerceGseaFields(value);
}

function createTemplateId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function safeLocalStorageGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeLocalStorageSet(key: string, value: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function coerceTemplate(value: unknown): RunTemplate | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (typeof record.id !== "string" || !isAnalysisKind(record.kind)) return null;
  if (typeof record.name !== "string") return null;
  const fields = coerceFields(record.kind, record.fields);
  if (fields === null) return null;
  const now = new Date().toISOString();
  return {
    id: record.id,
    kind: record.kind,
    name: record.name,
    fields,
    created_at: typeof record.created_at === "string" ? record.created_at : now,
    updated_at: typeof record.updated_at === "string" ? record.updated_at : now
  };
}

function readAllTemplates(): RunTemplate[] {
  const raw = safeLocalStorageGet(RUN_TEMPLATES_STORAGE_KEY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    const values =
      parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? Object.values(parsed)
        : Array.isArray(parsed)
          ? parsed
          : [];
    return values
      .map(coerceTemplate)
      .filter((template): template is RunTemplate => template !== null);
  } catch {
    return [];
  }
}

function writeAllTemplates(templates: RunTemplate[]): void {
  const sorted = [...templates]
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 100);
  safeLocalStorageSet(
    RUN_TEMPLATES_STORAGE_KEY,
    JSON.stringify(Object.fromEntries(sorted.map((template) => [template.id, template])))
  );
}

function limitTemplateText(kind: AnalysisKind, fields: object): object {
  const record = fields as Record<string, unknown>;
  if (kind === "set_similarity" && typeof record.genes === "string") {
    return {
      ...record,
      genes:
        record.genes.length > MAX_TEMPLATE_TEXT_CHARS
          ? ""
          : record.genes,
      genesTextOmitted: record.genes.length > MAX_TEMPLATE_TEXT_CHARS
    };
  }
  if (kind === "gsea" && typeof record.ranked === "string") {
    return {
      ...record,
      ranked:
        record.ranked.length > MAX_TEMPLATE_TEXT_CHARS
          ? ""
          : record.ranked,
      rankedTextOmitted: record.ranked.length > MAX_TEMPLATE_TEXT_CHARS
    };
  }
  return fields;
}

export function getRunTemplates<TFields extends object>(
  kind: AnalysisKind
): RunTemplate<TFields>[] {
  return readAllTemplates()
    .filter((template) => template.kind === kind)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at)) as RunTemplate<TFields>[];
}

export function saveRunTemplate<TFields extends object>(
  kind: AnalysisKind,
  name: string,
  fields: TFields,
  existingId?: string
): RunTemplate<TFields> {
  const allTemplates = readAllTemplates();
  const existing = existingId
    ? allTemplates.find((template) => template.id === existingId)
    : undefined;
  const now = new Date().toISOString();
  const limitedFields = limitTemplateText(kind, fields);
  const template: RunTemplate<TFields> = {
    id: existing?.id ?? createTemplateId(),
    kind,
    name: name.trim() || "Untitled template",
    fields: limitedFields as TFields,
    created_at: existing?.created_at ?? now,
    updated_at: now
  };
  writeAllTemplates([
    template as RunTemplate,
    ...allTemplates.filter((item) => item.id !== template.id)
  ]);
  return template;
}

export function deleteRunTemplate(templateId: string): void {
  writeAllTemplates(readAllTemplates().filter((template) => template.id !== templateId));
}
