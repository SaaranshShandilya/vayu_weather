export type SessionState = {
  authenticated: boolean;
  configured: boolean;
};

export type DataInfo = {
  ready: boolean;
  first_date?: string;
  last_date?: string;
  records?: number;
  cities?: number;
  states?: number;
  stations?: number;
  sources?: DataSourceInfo[];
};

export type DataSourceInfo = {
  id: string;
  title: string;
  provider: string;
  date_start: string | null;
  date_end: string | null;
  temporal_granularity: string;
  geographic_granularity: string;
  records: number;
  quality: string;
  provenance: string;
};

export type Visualization =
  | "none"
  | "bar"
  | "line"
  | "scatter"
  | "map_points"
  | "choropleth"
  | "grid_heatmap";

export type GridPayload = {
  latitudes: number[];
  longitudes: number[];
  frames: Array<{ label: string; values: Array<number | null> }>;
  value_label: string;
  unit: string;
  scale: "pm25" | "pm10" | "hours";
  scale_max: number | null;
  landmarks: Array<{ name: string; latitude: number; longitude: number }>;
  source_shape: number[];
  resolution_degrees: number;
  synthetic: boolean;
};

export type AnalysisStep = {
  function_name: string;
  arguments: Record<string, unknown>;
};

export type ResultData = {
  columns: string[];
  rows: Array<Record<string, string | number | null>>;
  visualization: Visualization;
  title: string;
  x_key: string;
  y_keys: string[];
  truncated: boolean;
  grid?: GridPayload | null;
};

export type AnalysisResultSet = ResultData & {
  analysis_function: string;
};

export type ChatResult = ResultData & {
  answer: string;
  key_points: string[];
  method_note: string;
  analysis_steps: AnalysisStep[];
  analysis_type: string;
  elapsed_ms: number;
  suggested_questions: string[];
  clarification_options: string[];
  result_sets: AnalysisResultSet[];
};

export type ConversationMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: ChatResult;
  error?: string;
  retryQuestion?: string;
};
