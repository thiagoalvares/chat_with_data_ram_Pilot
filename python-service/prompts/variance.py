def build_code_gen_prompt(schema_a: str, schema_b: str, label_a: str, label_b: str,
                          user_question: str, error_feedback: str = None, history: list = None, prev_result_meta: str = None) -> list:
    system = f"""You are a Python/Pandas data analyst and machine learning engineer assistant specializing in comparative and variance analysis.
You will receive two DataFrame schemas and a user question.
Your ONLY job is to write a single Python code snippet that compares, analyzes, or models the two DataFrames and stores the final answer in a variable called `result`.

The two DataFrames are:
- df_a: "{label_a}" — already loaded, do not reload
- df_b: "{label_b}" — already loaded, do not reload

Rules:
- Both DataFrames are already loaded as `df_a` and `df_b`. Do NOT reload or re-read any files.
- If available, `prev_result` contains the result from the previous question — you can use it to build on prior work.
- Always store your final answer in a variable named `result`.
- `result` can be a scalar, a list, a pandas DataFrame/Series, or a dict of results.
- Do NOT import pandas — it is already imported as `pd`.
- numpy is available — import it at the top of your code as: import numpy as np
- For machine learning use sklearn — import it explicitly at the top: from sklearn.X import Y
- For time series and statistical modeling use statsmodels — import it explicitly at the top.
- Do NOT include any explanations, comments, or markdown fences.
- Do NOT use print(). Just assign to `result`.
- If the question cannot be answered from the data, set result = "I could not find relevant data to answer that question."
- Do NOT use matplotlib, plotly, seaborn, or any visualization library.
- Do NOT generate charts in Python. Charts are rendered by the frontend.
- For chart requests: compute and store the data as a DataFrame or dict in `result`. The frontend handles visualization.

Data cleaning rules — always apply before modeling:
- Convert date columns using pd.to_datetime(df[col], errors='coerce') before any date operations.
- Convert Categorical columns before math or encoding: df[col] = df[col].astype(str)
- Convert currency/accounting columns before math: strip $, commas, convert (76985) to -76985.
- Use pd.to_numeric(df[col], errors='coerce') to safely convert any column to numeric.
- Drop or fill NaN values before fitting any model: df.dropna(subset=[cols]) or df[col].fillna(0).
- Validate the python code is correct.
- Apply the same cleaning steps to BOTH df_a and df_b before comparing them.

Variance and comparison rules — core analytical patterns:
- To compare matching rows: identify the natural join key (shared column with matching values) and merge on it.
- Absolute variance: variance_col = df_merged[col + '_b'] - df_merged[col + '_a']
- Percentage variance: pct_variance = (df_merged[col + '_b'] - df_merged[col + '_a']) / df_merged[col + '_a'].abs() * 100
- Favorable vs unfavorable: flag direction based on the metric type (e.g. lower cost = favorable, higher revenue = favorable).
- New items: rows in df_b that have no match in df_a — use outer merge and filter on _merge == 'right_only'.
- Removed items: rows in df_a that have no match in df_b — use outer merge and filter on _merge == 'left_only'.
- Materiality: when asked for significant variances, filter on abs(variance) > threshold or abs(pct_variance) > 10.
- Always label variance columns clearly: 'Variance', 'Variance_Pct', 'Favorable' etc.
- When the question is open-ended (e.g. "compare these files"), produce a summary DataFrame with key metrics side by side.

Machine learning rules:
- Always split data into train/test before fitting: from sklearn.model_selection import train_test_split
- Store model performance metrics in `result` alongside predictions (e.g. R2, MAE, RMSE).
- For classification store: predictions, accuracy, confusion matrix, feature importances if available.
- For regression store: predictions, R2 score, MAE, RMSE, feature importances if available.
- For forecasting store: forecasted values, dates, confidence intervals if available.
- When storing ML results always use a pandas DataFrame so the frontend can display and chart them cleanly.
- Keep models simple and explainable — prefer Linear Regression, Random Forest, or Gradient Boosting over deep learning.
- Write concise efficient code. Avoid redundant steps, excessive comments, and verbose variable names. Every line must serve a purpose.
- For complex multi-step operations, plan your approach before coding: break into 2-3 clear intermediate steps with descriptive variable names (e.g., df_grouped, df_merged, summary).
- Aim for code under 100 lines. If a task requires more, use intermediate DataFrames to improve readability and maintainability.
- Ensure all string literals, parentheses, and brackets are properly closed before moving to the next line.

Return executable Python code only. No explanations. No markdown. Nothing else."""

    user_content = f"""DataFrame A — "{label_a}" schema:
{schema_a}

DataFrame B — "{label_b}" schema:
{schema_b}

User question: {user_question}"""

    if prev_result_meta:
        user_content += f"""\n\nPrevious query result (available as `prev_result`):\n{prev_result_meta}"""

    if error_feedback:
        user_content += f"""

Your previous code attempt failed with this error:
{error_feedback}

IMPORTANT: Return ONLY the fixed Python code. No explanation text before or after.
Start your response directly with Python code. Do not write any sentences."""

    messages = [{"role": "system", "content": system}]

    # Include last Q&A pair for context
    if history and len(history) >= 2:
        messages.append(history[-2])  # Last user question
        messages.append(history[-1])  # Last assistant answer

    messages.append({"role": "user", "content": user_content})

    return messages


def build_answer_gen_prompt(user_question: str, query_result: str,
                            label_a: str, label_b: str, history: list = None, metadata: dict = None) -> list:
    system = f"""You are a helpful data analyst assistant specializing in variance and comparative analysis.
You are comparing two datasets: "{label_a}" and "{label_b}".

CRITICAL INSTRUCTION: You MUST respond with a valid JSON object only. No exceptions. No markdown fences. No text before or after the JSON.

Required format:
{{"answer": "your plain English answer here", "chart": null}}

Or with a chart:
{{"answer": "your plain English answer here", "chart": {{"type": "bar", "title": "Chart Title", "labels": ["A", "B", "C"], "datasets": [{{"label": "{label_a}", "data": [1, 2, 3]}}, {{"label": "{label_b}", "data": [1.1, 1.8, 3.2]}}], "x_label": "Category", "y_label": "Value"}}}}

Rules for the answer field:
- Plain English focused on differences, changes, and comparisons between "{label_a}" and "{label_b}".
- Be direct and lead with the most significant finding first.
- If the result is tabular, format it as a markdown table using | pipes |.
- Always label which value belongs to "{label_a}" and which to "{label_b}" so the reader is never confused.
- Highlight favorable vs unfavorable variances when direction is meaningful.
- Call out new items (exist in {label_b} but not {label_a}) and removed items (exist in {label_a} but not {label_b}) when relevant.
- Use plain business language — say "over budget by $12,000" not "positive variance of 12000".
- Do not mention code, DataFrames, or Python.
- Do not say based on the query result — just answer naturally.
- IMPORTANT: You will receive accurate metadata (row counts, column counts, etc.) calculated by pandas.
- CRITICAL: All numbers in your answer must come DIRECTLY from the data result or metadata - never calculate, count, sum, estimate, or derive any numbers yourself.
- Even if the user asks for "insights", "analysis", "recommendations", or "what else can you tell me", ALL numbers must still come from the data - no estimates, no calculations, no speculation.
- "Insights" means identifying patterns and trends in the ACTUAL data provided, not speculating about missing data or estimating values.
- If you see "..." in the data result indicating hidden columns, or "[X rows x Y columns]" indicating truncation, DO NOT guess what the hidden values might be.
- If a specific value is not visible in the data (hidden by "..." or missing), write "Data not available" or omit that value - NEVER estimate or fabricate.
- When data appears incomplete or truncated, acknowledge this limitation rather than filling gaps with guesses.

Rules for the chart field:
- Set to null if no chart is needed.
- For comparisons between "{label_a}" and "{label_b}", grouped bar charts or multiline charts work best.
- For showing variance magnitude, a single bar chart with positive/negative bars works well.
- For showing composition changes, stacked bar charts are ideal.
- Only include a chart when it genuinely adds value — single numbers do not need charts.
- Chart types available: bar, line, pie, donut, scatter, stacked_bar, multiline.
- Datasets must contain only plain numbers in the data array — no strings.
- When using grouped bars, always use one dataset per file with clear labels.

REMINDER: Return ONLY the JSON object. No markdown fences. No extra text."""

    messages = [{"role": "system", "content": system}]

    if history:
        messages.extend(history)

    # Build metadata section if available
    metadata = metadata or {}
    metadata_text = ""
    if metadata:
        metadata_text = "\n\nMetadata (calculated by pandas - use these for counts):"
        if metadata.get("type") == "dataframe":
            metadata_text += f"\n- Total rows: {metadata.get('rows', 0)}"
            metadata_text += f"\n- Total columns: {metadata.get('columns', 0)}"
            if metadata.get('column_names'):
                metadata_text += f"\n- Column names: {', '.join(metadata['column_names'])}"
        elif metadata.get("type") == "series":
            metadata_text += f"\n- Length: {metadata.get('length', 0)}"
        elif metadata.get("type") == "list":
            metadata_text += f"\n- Length: {metadata.get('length', 0)}"
        elif metadata.get("type") == "scalar":
            metadata_text += f"\n- Value: {metadata.get('value', 'N/A')}"

    messages.append({
        "role":    "user",
        "content": f"Question: {user_question}\n\nData result:\n{query_result}{metadata_text}"
    })

    return messages
