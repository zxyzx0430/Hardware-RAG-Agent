export interface KBItem {
  id: string;
  name: string;
  size: string;
  chunks: number;
  status: "indexed" | "indexing" | "error";
  enabled: boolean;
  updatedAt: string;
  docType: string;
  tags: string[];
  errorMessage?: string;
}
