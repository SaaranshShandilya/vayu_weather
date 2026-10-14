const metricLabels: Record<string, string> = {
  pm25: "PM2.5",
  pm10: "PM10",
  no: "NO",
  no2: "NO₂",
  nox: "NOx",
  nh3: "NH₃",
  so2: "SO₂",
  co: "CO",
  ozone: "Ozone",
  temperature: "Temperature",
  humidity: "Relative humidity",
  wind_speed: "Wind speed",
  wind_direction: "Wind direction",
  rainfall: "Rainfall",
  total_rainfall: "Total rainfall",
  solar_radiation: "Solar radiation",
  pressure: "Pressure",
  vertical_wind_speed: "Vertical wind speed",
};

const metricUnits: Record<string, string> = {
  pm25: "µg/m³",
  pm10: "µg/m³",
  no: "µg/m³",
  no2: "µg/m³",
  nox: "ppb",
  nh3: "µg/m³",
  so2: "µg/m³",
  co: "mg/m³",
  ozone: "µg/m³",
  temperature: "°C",
  humidity: "%",
  wind_speed: "m/s",
  wind_direction: "degrees",
  rainfall: "mm",
  total_rainfall: "mm",
  solar_radiation: "W/m²",
  pressure: "mmHg",
  vertical_wind_speed: "m/s",
};

const directLabels: Record<string, string> = {
  city: "City",
  state: "State",
  station: "Monitoring station",
  period: "Period",
  date: "Date",
  season: "Season",
  year: "Year",
  month: "Month",
  day_type: "Day type",
  condition_group: "Condition",
  funding_group: "Funding group",
  pearson_r: "Pearson r",
  station_count: "Qualifying stations",
  city_count: "Qualifying cities",
  observation_days: "Observation days",
  observed_day_count: "Observed days",
  exceedance_day_count: "Exceedance days",
  exceedance_share_pct: "Exceedance share",
  total_station_days: "Station-days",
  paired_days: "Paired observations",
  paired_cities: "Paired cities",
  paired_states: "Paired states",
  population: "Population",
  population_2011: "Population (Census 2011)",
  population_density: "Population density",
  population_density_2011_per_km2: "Population density (Census 2011)",
  area_km2: "Area",
  total_fund_released: "Total funds released",
  utilisation_june_2022: "Utilisation (June 2022)",
  released_crore: "Funds released",
  utilised_crore: "Funds utilised",
  utilisation_pct: "Utilisation",
  reported_value: "Reported value",
  value: "Value",
  average_value: "Average value",
  forecast_time: "Forecast time (IST)",
  lead_hour: "Lead hour",
  forecast_day: "Forecast day",
  mean_area_above_pct: "Mean area above threshold",
  peak_area_above_pct: "Peak area above threshold",
};

const directUnits: Record<string, string> = {
  exceedance_share_pct: "%",
  population_density: "people/km²",
  population_density_2011_per_km2: "people/km²",
  area_km2: "km²",
  total_fund_released: "₹ crore",
  utilisation_june_2022: "%",
  released_crore: "₹ crore",
  utilised_crore: "₹ crore",
  utilisation_pct: "%",
  mean_area_above_pct: "%",
  peak_area_above_pct: "%",
};

const statisticalPrefixes: Array<[string, string]> = [
  ["mean_", "Mean "],
  ["max_", "Peak "],
  ["median_", "Median "],
  ["average_", "Average "],
  ["start_", "Start "],
  ["end_", "End "],
  ["change_", "Change in "],
];

function metricParts(key: string) {
  for (const [prefix, label] of statisticalPrefixes) {
    if (key.startsWith(prefix)) {
      const metric = key.slice(prefix.length);
      if (metricLabels[metric]) {
        return { label: `${label}${metricLabels[metric]}`, metric };
      }
    }
  }
  if (metricLabels[key]) {
    return { label: metricLabels[key], metric: key };
  }
  return null;
}

export function chartLabel(key: string) {
  const metric = metricParts(key);
  if (metric) return metric.label;
  if (directLabels[key]) return directLabels[key];
  return key
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function chartUnit(key: string) {
  const metric = metricParts(key);
  if (metric) return metricUnits[metric.metric] ?? "";
  return directUnits[key] ?? "";
}

export function chartAxisTitle(key: string) {
  const label = chartLabel(key);
  const unit = chartUnit(key);
  return unit ? `${label} (${unit})` : label;
}

export function formatChartTick(value: unknown, key: string) {
  if (typeof value === "number") {
    return new Intl.NumberFormat("en-IN", {
      maximumFractionDigits: 2,
      notation: Math.abs(value) >= 100_000 ? "compact" : "standard",
    }).format(value);
  }
  const text = String(value ?? "");
  if (key === "date" && /^\d{4}-\d{2}-\d{2}/.test(text)) {
    return text.slice(0, 10);
  }
  return text;
}
