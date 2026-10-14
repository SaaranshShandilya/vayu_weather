import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ResultData } from "./types";
import {
  chartAxisTitle,
  formatChartTick,
} from "./chartMetadata";
import { GeoMap } from "./GeoMap";
import { GridHeatmap } from "./GridHeatmap";

const seriesColors = ["#36589f", "#6379ad", "#8e9bc0", "#203968"];

export function AnalysisChart({ result }: { result: ResultData }) {
  if (result.visualization === "grid_heatmap") {
    return result.grid ? <GridHeatmap grid={result.grid} title={result.title} /> : null;
  }
  if (
    result.visualization === "map_points" ||
    result.visualization === "choropleth"
  ) {
    return <GeoMap result={result} />;
  }
  if (
    result.visualization === "none" ||
    !result.rows.length ||
    !result.x_key ||
    !result.y_keys.length
  ) {
    return null;
  }
  const angledCategoryLabels =
    result.visualization === "bar" &&
    result.rows.length <= 12 &&
    result.rows.some(
      (row) => String(row[result.x_key] ?? "").length > 9,
    );
  const longestCategory = Math.max(
    ...result.rows.map((row) => String(row[result.x_key] ?? "").length),
  );
  const useHorizontalBars =
    result.visualization === "bar" &&
    (result.x_key === "station" || longestCategory > 18);
  const xAxisTitle = chartAxisTitle(result.x_key);
  const primaryYKey = result.y_keys[0]!;
  const yAxisTitle =
    result.y_keys.length === 1
      ? chartAxisTitle(primaryYKey)
      : "Value";
  const chartNote =
    result.visualization === "scatter" &&
    typeof result.rows[0]?.paired_days === "number" &&
    result.rows[0].paired_days > result.rows.length
      ? (
          `${result.rows.length.toLocaleString("en-IN")} plotted points · ` +
          `${result.rows[0].paired_days.toLocaleString("en-IN")} paired ` +
          "observations used for the statistic"
        )
      : null;

  const common = (
    <>
      <CartesianGrid stroke="#d9d8d1" strokeDasharray="3 5" vertical={false} />
      <XAxis
        dataKey={result.x_key}
        tick={{ fill: "#62625e", fontSize: 11 }}
        tickLine={false}
        axisLine={{ stroke: "#c4c3bc" }}
        tickFormatter={(value) => formatChartTick(value, result.x_key)}
        interval={
          result.visualization === "bar" && result.rows.length <= 12
            ? 0
            : "preserveStartEnd"
        }
        minTickGap={18}
        angle={angledCategoryLabels ? -25 : 0}
        textAnchor={angledCategoryLabels ? "end" : "middle"}
        height={angledCategoryLabels ? 78 : 56}
        tickMargin={angledCategoryLabels ? 8 : 5}
        label={{
          value: xAxisTitle,
          position: "insideBottom",
          offset: -10,
          fill: "#454541",
          fontSize: 12,
          fontWeight: 600,
        }}
      />
      <YAxis
        tick={{ fill: "#62625e", fontSize: 11 }}
        tickLine={false}
        axisLine={false}
        tickFormatter={(value) => formatChartTick(value, primaryYKey)}
        width={76}
        label={{
          value: yAxisTitle,
          angle: -90,
          position: "insideLeft",
          offset: 2,
          fill: "#454541",
          fontSize: 12,
          fontWeight: 600,
        }}
        domain={
          result.visualization === "bar"
            ? [
                (dataMin: number) => Math.min(dataMin, 0),
                (dataMax: number) => Math.max(dataMax, 0),
              ]
            : undefined
        }
      />
      <Tooltip
        cursor={
          result.visualization === "bar"
            ? false
            : { stroke: "#a7a69f", strokeDasharray: "3 3" }
        }
        formatter={(value, name) => [
          formatChartTick(value, ""),
          String(name),
        ]}
        labelFormatter={(label) =>
          `${xAxisTitle}: ${formatChartTick(label, result.x_key)}`
        }
        contentStyle={{
          background: "#fcfbf8",
          border: "1px solid #d9d8d1",
          borderRadius: "8px",
          boxShadow: "0 12px 34px rgba(30, 42, 67, 0.12)",
          fontSize: "12px",
        }}
      />
      {result.y_keys.length > 1 ? (
        <Legend wrapperStyle={{ fontSize: "12px", paddingTop: "12px" }} />
      ) : null}
    </>
  );

  return (
    <figure className="analysis-chart" aria-label={result.title || "Analysis chart"}>
      {result.title ? <figcaption>{result.title}</figcaption> : null}
      <p className="chart-dimensions">
        {xAxisTitle} <span aria-hidden="true">×</span> {yAxisTitle}
      </p>
      <div className="chart-frame">
        <ResponsiveContainer width="100%" height="100%">
          {result.visualization === "bar" ? (
            useHorizontalBars ? (
              <BarChart
                data={result.rows}
                layout="vertical"
                margin={{ top: 8, right: 28, left: 10, bottom: 32 }}
              >
                <CartesianGrid
                  stroke="#d9d8d1"
                  strokeDasharray="3 5"
                  horizontal={false}
                />
                <XAxis
                  type="number"
                  tick={{ fill: "#62625e", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "#c4c3bc" }}
                  tickFormatter={(value) => formatChartTick(value, primaryYKey)}
                  label={{
                    value: yAxisTitle,
                    position: "insideBottom",
                    offset: -18,
                    fill: "#454541",
                    fontSize: 12,
                    fontWeight: 600,
                  }}
                />
                <YAxis
                  type="category"
                  dataKey={result.x_key}
                  width={178}
                  tick={{ fill: "#62625e", fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) => {
                    const text = String(value ?? "");
                    return text.length > 27 ? `${text.slice(0, 26)}…` : text;
                  }}
                />
                <Tooltip
                  cursor={false}
                  formatter={(value, name) => [
                    formatChartTick(value, ""),
                    String(name),
                  ]}
                  labelFormatter={(label) =>
                    `${xAxisTitle}: ${String(label)}`
                  }
                  contentStyle={{
                    background: "#fcfbf8",
                    border: "1px solid #d9d8d1",
                    borderRadius: "8px",
                    boxShadow: "0 12px 34px rgba(30, 42, 67, 0.12)",
                    fontSize: "12px",
                  }}
                />
                {result.y_keys.map((key, index) => (
                  <Bar
                    key={key}
                    dataKey={key}
                    name={chartAxisTitle(key)}
                    fill={seriesColors[index % seriesColors.length]}
                    radius={[0, 4, 4, 0]}
                    maxBarSize={30}
                  />
                ))}
              </BarChart>
            ) : (
              <BarChart data={result.rows} margin={{ top: 10, right: 28, left: 10, bottom: 10 }}>
                {common}
                {result.y_keys.map((key, index) => (
                  <Bar
                    key={key}
                    dataKey={key}
                    name={chartAxisTitle(key)}
                    fill={seriesColors[index % seriesColors.length]}
                    radius={[4, 4, 0, 0]}
                    maxBarSize={44}
                  />
                ))}
              </BarChart>
            )
          ) : result.visualization === "scatter" ? (
            <ScatterChart margin={{ top: 10, right: 18, left: 16, bottom: 34 }}>
              <CartesianGrid stroke="#d9d8d1" strokeDasharray="3 5" />
              <XAxis
                type="number"
                dataKey={result.x_key}
                name={xAxisTitle}
                tick={{ fill: "#62625e", fontSize: 11 }}
                tickFormatter={(value) => formatChartTick(value, result.x_key)}
                tickLine={false}
                axisLine={{ stroke: "#c4c3bc" }}
                tickCount={6}
                label={{
                  value: xAxisTitle,
                  position: "insideBottom",
                  offset: -18,
                  fill: "#454541",
                  fontSize: 12,
                  fontWeight: 600,
                }}
              />
              <YAxis
                type="number"
                dataKey={primaryYKey}
                name={yAxisTitle}
                tick={{ fill: "#62625e", fontSize: 11 }}
                tickFormatter={(value) => formatChartTick(value, primaryYKey)}
                tickLine={false}
                axisLine={false}
                tickCount={6}
                width={76}
                label={{
                  value: yAxisTitle,
                  angle: -90,
                  position: "insideLeft",
                  offset: 2,
                  fill: "#454541",
                  fontSize: 12,
                  fontWeight: 600,
                }}
              />
              <Tooltip
                cursor={{ strokeDasharray: "3 3" }}
                formatter={(value, name) => [
                  formatChartTick(value, ""),
                  String(name),
                ]}
                contentStyle={{
                  background: "#fcfbf8",
                  border: "1px solid #d9d8d1",
                  borderRadius: "8px",
                  boxShadow: "0 12px 34px rgba(30, 42, 67, 0.12)",
                  fontSize: "12px",
                }}
              />
              <Scatter
                data={result.rows}
                fill={seriesColors[0]}
                fillOpacity={0.58}
              />
            </ScatterChart>
          ) : (
            <LineChart data={result.rows} margin={{ top: 10, right: 30, left: 10, bottom: 10 }}>
              {common}
              {result.y_keys.map((key, index) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  name={chartAxisTitle(key)}
                  stroke={seriesColors[index % seriesColors.length]}
                  strokeWidth={2.25}
                  dot={result.rows.length < 20 ? { r: 2.5 } : false}
                  activeDot={{ r: 4 }}
                />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
      {chartNote ? <p className="chart-note">{chartNote}</p> : null}
    </figure>
  );
}
