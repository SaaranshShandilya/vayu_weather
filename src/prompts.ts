export type PromptGroup = {
  name: string;
  label: string;
  prompts: string[];
};

export const promptGroups: PromptGroup[] = [
  {
    name: "start",
    label: "Rankings & thresholds",
    prompts: [
      "Which city had the highest average PM2.5 in 2023?",
      "Which station had the highest average PM2.5 in Delhi during 2023?",
      "Rank the 10 cities with the highest average PM2.5.",
      "Which cities exceed 60 µg/m³ average PM2.5?",
      "Compare average PM2.5 in Delhi, Mumbai, Kolkata, and Chennai in 2023.",
      "Which 10 cities had the lowest average PM10 in 2022?",
      "How many cities averaged below 40 µg/m³ PM2.5 in 2023?",
    ],
  },
  {
    name: "trends",
    label: "Trends & seasons",
    prompts: [
      "Show monthly PM2.5 trends for Delhi in 2023.",
      "Show Delhi's yearly PM2.5 trend from 2018 to 2025.",
      "Compare Delhi's average PM2.5 in 2024 and 2025.",
      "Compare PM2.5 levels between winter and summer.",
      "Show the seasonal PM2.5 profile for Mumbai.",
      "Show seasonal PM10 patterns across Indian cities.",
      "Did Delhi's average PM2.5 improve from 2019 to 2023?",
      "Which cities reduced PM2.5 the most between 2022 and 2023?",
      "Which cities had the largest increase in PM10 from 2021 to 2023?",
    ],
  },
  {
    name: "weather",
    label: "Weather relationships",
    prompts: [
      "How does rainfall relate to PM2.5 during monsoon months?",
      "Does wind speed above 3 m/s coincide with lower PM2.5 in Delhi?",
      "Which meteorological factor is most correlated with PM2.5?",
      "How does temperature relate to PM2.5 in Delhi during March–May 2024?",
      "What is the PM2.5–humidity relationship in Delhi during winter?",
      "How strongly is wind speed associated with PM10 across city-days?",
    ],
  },
  {
    name: "pollutants",
    label: "Pollutant relationships",
    prompts: [
      "Show the relationship between PM2.5 and PM10.",
      "Compare weekday and weekend PM2.5 levels in Delhi in 2024.",
      "How do NO2 and PM2.5 vary together across cities?",
      "Show seasonal PM2.5 patterns across India.",
      "Rank cities by average NO2 in 2023.",
      "Which cities exceeded 100 µg/m³ average PM10 in 2023?",
      "Which cities had the highest share of days above 60 µg/m³ PM2.5 in 2023?",
      "How many observed days did Delhi exceed 60 µg/m³ PM2.5 in 2023?",
    ],
  },
  {
    name: "policy",
    label: "Funding & policy",
    prompts: [
      "How much NCAP funding did Delhi receive compared with Mumbai?",
      "Which NCAP-funded cities still exceed 60 µg/m³ PM2.5?",
      "Compare PM2.5 in high-funded and low-funded NCAP cities.",
      "Show state-wise average PM2.5 for policy planning.",
      "Rank NCAP cities by total funds released.",
      "Is total NCAP funding associated with PM2.5 change from 2019 to 2025?",
      "Which NCAP-funded cities reduced PM2.5 the most between 2022 and 2023?",
      "Is total NCAP funding associated with PM2.5 change from 2019 to 2023?",
      "Is state NCAP utilisation associated with PM2.5 change from 2022 to 2023?",
      "Is state population density associated with average PM2.5 in 2023?",
      "Rank states by recorded NCAP utilisation as of June 2022.",
      "Show combined NCAP and XV-FC funds released and utilised by fiscal year.",
      "What physical progress has NCAP and XV-FC reported as of August 2025?",
      "Rank states by Census 2011 population density.",
      "What is the CPCB 24-hour PM2.5 standard?",
    ],
  },
  {
    name: "maps",
    label: "Maps & geography",
    prompts: [
      "Map all CPCB monitoring stations in India.",
      "Map stations with qualifying PM2.5 data in 2025.",
      "Show a state choropleth of active monitoring-station counts in 2025.",
      "Show a state choropleth of average PM2.5 in 2025.",
      "What data sources and date ranges are available?",
    ],
  },
  {
    name: "cams_forecast",
    label: "CAMS air-quality forecast (next 5 days)",
    // Relative days resolve against today's date and the latest CAMS run,
    // which the router is given with every question.
    prompts: [
      "Map the PM2.5 forecast across India for tomorrow.",
      "What will PM2.5 be in Lucknow each day this week?",
      "Animate the hourly PM2.5 forecast around Delhi for the next 2 days.",
      "Animate the hourly PM2.5 forecast across India for tomorrow.",
      "Which states have the highest forecast PM2.5 tomorrow?",
      "Map forecast hours with PM2.5 above 60 µg/m³ across Uttar Pradesh.",
      "Show the hourly ozone forecast for Kolkata tomorrow.",
      "Map the dust aerosol optical depth forecast over Rajasthan.",
      "Which cities have the highest forecast NO2 tomorrow?",
    ],
  },
  {
    name: "methods",
    label: "Coverage & methods",
    prompts: [
      "Which cities have the most PM2.5 monitoring stations?",
      "Show PM2.5 observation coverage by city in 2023.",
      "How many paired days support the rainfall–PM2.5 correlation?",
      "Compare Delhi and Mumbai's mean PM2.5 in 2024 using only stations with at least 30 days of data.",
      "Which years have the strongest PM2.5 data coverage?",
    ],
  },
  {
    name: "composition",
    label: "Composition & advanced",
    prompts: [
      "Compare Delhi and Mumbai's mean PM2.5 and monitoring-station coverage in 2023.",
      "For Delhi, show the yearly PM2.5 trend and its seasonal profile.",
      "Which NCAP cities exceed 60 µg/m³ PM2.5, and is funding associated with lower PM2.5?",
      "Compare Delhi's monthly PM2.5 trend with its rainfall–PM2.5 relationship in 2023.",
      "Compare Mumbai's mean and median PM2.5 from 2019 to 2023.",
      "Rank cities by PM2.5 and compare that ranking with monitoring coverage.",
      "Compare Delhi and Mumbai's PM2.5 change and station coverage from 2022 to 2023.",
      "Which NCAP-funded cities reduced PM2.5 most from 2017 to 2025, and is total funding associated with that change?",
    ],
  },
];

export const featuredPrompts = [
  promptGroups[0]!.prompts[0]!,
  promptGroups[0]!.prompts[1]!,
  promptGroups[1]!.prompts[0]!,
  promptGroups[2]!.prompts[0]!,
  promptGroups[4]!.prompts[6]!,
];
