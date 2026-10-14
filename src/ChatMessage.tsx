import { lazy, Suspense } from "react";
import { BrandLogo } from "./BrandLogo";
import { ResultTable } from "./ResultTable";
import { DatabaseIcon } from "./icons";
import type { ConversationMessage } from "./types";

const AnalysisChart = lazy(() =>
  import("./AnalysisChart").then((module) => ({
    default: module.AnalysisChart,
  })),
);

const analysisLabels: Record<string, string> = {
  rank_cities: "City ranking",
  city_average: "City average",
  threshold_cities: "Threshold analysis",
  compare_cities: "City comparison",
  time_trend: "Time trend",
  relationship: "Relationship analysis",
  strongest_weather_relationship: "Weather-factor ranking",
  seasonal_profile: "Seasonal profile",
  funding_lookup: "NCAP funding",
  station_coverage: "Monitoring coverage",
  rank_stations: "Station ranking",
  weekday_weekend_profile: "Weekday/weekend comparison",
  condition_comparison: "Threshold-group comparison",
  rank_states: "State ranking",
  coverage_trend: "Coverage trend",
  funding_rank: "NCAP funding ranking",
  ncap_threshold_cities: "NCAP pollution threshold",
  ncap_funding_groups: "NCAP funding groups",
  pollution_change: "Period change",
  context_relationship: "Context relationship",
  threshold_frequency: "Threshold frequency",
  station_map: "Station map",
  state_choropleth: "State choropleth",
  data_catalog: "Source catalog",
  census_state_rank: "Census context",
  air_quality_standard: "CPCB standard",
  programme_finance_trend: "Programme finance",
  physical_progress: "Programme progress",
  forecast_grid_map: "Gridded forecast map",
  forecast_point_series: "Gridded forecast at a place",
  forecast_exceedance_map: "Forecast exceedance map",
  clarify_question: "Clarification needed",
  analysis_unavailable: "Analysis needs refinement",
  composition: "Composed analysis",
};

function formatArgument(name: string, value: unknown) {
  if (Array.isArray(value)) {
    if (value.length === 0) {
      if (name === "months") return "all months";
      if (name === "cities") return "all cities";
      return "none";
    }
    return value.map((item) => String(item)).join(", ");
  }
  if (typeof value === "string") {
    return value.replaceAll("_", " ");
  }
  return String(value);
}

export function ChatMessage({
  message,
  onPrompt,
}: {
  message: ConversationMessage;
  onPrompt: (prompt: string) => void;
}) {
  if (message.role === "user") {
    return (
      <article className="message message--user">
        <div className="user-bubble">{message.content}</div>
      </article>
    );
  }

  return (
    <article className={`message message--assistant ${message.error ? "has-error" : ""}`}>
      <BrandLogo className="brand-mark--avatar" />
      <div className="assistant-body">
        <div className="assistant-meta">
          <strong>VayuChat</strong>
          <span>Gemini 3.5 Flash-Lite</span>
        </div>
        {message.error ? (
          <div className="answer-error" role="alert">
            <strong>Analysis stopped</strong>
            <p>{message.error}</p>
            {message.retryQuestion ? (
              <button onClick={() => onPrompt(message.retryQuestion!)}>
                Retry this question
              </button>
            ) : null}
          </div>
        ) : (
          <>
            <p className="answer-lead">{message.result?.answer ?? message.content}</p>
            {message.result?.clarification_options.length ? (
              <section
                className="clarification-options"
                aria-label="Clarification choices"
              >
                <p>Choose one to continue</p>
                <div>
                  {message.result.clarification_options.map((option) => (
                    <button key={option} onClick={() => onPrompt(option)}>
                      <strong>{option}</strong>
                      <span aria-hidden="true">→</span>
                    </button>
                  ))}
                </div>
              </section>
            ) : null}
            {message.result?.analysis_type ? (
              <div className="result-provenance" aria-label="Analysis provenance">
                <span>
                  <DatabaseIcon />
                  {analysisLabels[message.result.analysis_type] ?? "Validated analysis"}
                </span>
                {message.result.elapsed_ms > 0 ? (
                  <span>{(message.result.elapsed_ms / 1000).toFixed(1)}s</span>
                ) : null}
              </div>
            ) : null}
            {message.result?.key_points.length ? (
              <ul className="key-points">
                {message.result.key_points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            ) : null}
            {message.result ? (
              <div className="result-sets">
                {(
                  message.result.result_sets?.length > 1
                    ? message.result.result_sets
                    : [message.result]
                ).map((resultSet, index, all) => (
                  <section
                    className="result-set"
                    key={`${
                      "analysis_function" in resultSet
                        ? resultSet.analysis_function
                        : message.result?.analysis_type
                    }-${index}`}
                  >
                    {all.length > 1 ? (
                      <div className="result-set__heading">
                        <span>{String(index + 1).padStart(2, "0")}</span>
                        <strong>
                          {analysisLabels[
                            "analysis_function" in resultSet
                              ? resultSet.analysis_function
                              : message.result?.analysis_type ?? ""
                          ] ??
                            resultSet.title ??
                            "Verified result"}
                        </strong>
                      </div>
                    ) : null}
                    <Suspense
                      fallback={<div className="chart-loading" aria-hidden="true" />}
                    >
                      <AnalysisChart result={resultSet} />
                    </Suspense>
                    <ResultTable result={resultSet} />
                  </section>
                ))}
              </div>
            ) : null}
            {message.result?.method_note || message.result?.analysis_steps.length ? (
              <details className="method-details">
                <summary>
                  <DatabaseIcon />
                  Analysis recipe
                </summary>
                {message.result.analysis_steps.length ? (
                  <ol className="analysis-recipe">
                    {message.result.analysis_steps.map((step, index) => (
                      <li key={`${step.function_name}-${index}`}>
                        <div className="recipe-function">
                          <span>{String(index + 1).padStart(2, "0")}</span>
                          <code>{step.function_name}</code>
                        </div>
                        <dl>
                          {Object.entries(step.arguments).map(([name, value]) => (
                            <div key={name}>
                              <dt>{name.replaceAll("_", " ")}</dt>
                              <dd>{formatArgument(name, value)}</dd>
                            </div>
                          ))}
                        </dl>
                      </li>
                    ))}
                  </ol>
                ) : null}
                {message.result.method_note ? <p>{message.result.method_note}</p> : null}
              </details>
            ) : null}
            {message.result?.suggested_questions.length ? (
              <section className="follow-ups" aria-label="Suggested follow-up questions">
                <p>Ask a follow-up</p>
                <div>
                  {message.result.suggested_questions.map((question) => (
                    <button key={question} onClick={() => onPrompt(question)}>
                      {question}
                    </button>
                  ))}
                </div>
              </section>
            ) : null}
          </>
        )}
      </div>
    </article>
  );
}
