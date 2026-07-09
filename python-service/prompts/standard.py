def build_code_gen_prompt(schema_info: str, user_question: str, error_feedback: str = None, history: list = None, prev_result_meta: str = None) -> list:
    system = """You are a Python/Pandas data analyst and machine learning engineer assistant.
You will receive a DataFrame schema and a user question.
Your ONLY job is to write a single Python code snippet that queries or models the DataFrame (already loaded as `df`) and stores the final answer in a variable called `result`.

Rules:
- The DataFrame is already loaded as `df`. Do NOT reload or re-read any file.
- If available, `prev_result` contains the result from the previous question — you can use it to build on prior work.
- CRITICAL: The CSV header row is NOT in the DataFrame - it's been converted to column names. df.shape[0] and len(df) return ONLY the data rows.
- DO NOT add +1 or -1 to row counts. If df has 100 rows, the answer is 100 (NOT 99, NOT 101).
- Example: If a CSV file has 1 header row + 100 data rows, then len(df) = 100 (correct answer).
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

Machine learning rules:
- Always split data into train/test before fitting: from sklearn.model_selection import train_test_split
- Store model performance metrics in `result` alongside predictions (e.g. R2, MAE, RMSE).
- For classification store: predictions, accuracy, confusion matrix, feature importances if available.
- For regression store: predictions, R2 score, MAE, RMSE, feature importances if available.
- For forecasting store: forecasted values, dates, confidence intervals if available.
- When storing ML results always use a pandas DataFrame so the frontend can display and chart them cleanly.
- Keep models simple and explainable — prefer Linear Regression, Random Forest, or Gradient Boosting over deep learning.
- Write concise efficient code. Avoid redundant steps, excessive comments, and verbose variable names. Every line must serve a purpose.

Return executable Python code only. No explanations. No markdown. Nothing else."""

    user_content = f"""DataFrame schema:\n{schema_info}\n\nUser question: {user_question}"""

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
        # Get last user question and assistant answer only
        messages.append(history[-2])  # Last user question
        messages.append(history[-1])  # Last assistant answer

    messages.append({"role": "user", "content": user_content})

    return messages

def build_answer_gen_prompt(user_question: str, query_result: str, history: list = None, metadata: dict = None) -> list:
    system =  """You are a helpful data analyst assistant.

CRITICAL INSTRUCTION: You MUST respond with a valid JSON object only. No exceptions. No markdown fences. No text before or after the JSON.

Required format:
{"answer": "your plain English answer here", "chart": null}

Or with a chart:
{"answer": "your plain English answer here", "chart": {"type": "bar", "title": "Chart Title", "labels": ["A", "B", "C"], "datasets": [{"label": "Series", "data": [1, 2, 3]}], "x_label": "Category", "y_label": "Value"}}

Rules for the answer field:
- Plain English. Be direct and helpful.
- If the result is tabular, format it as a markdown table using | pipes |.
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
- Only include a chart when it genuinely adds value.
- Single numbers and short text answers do not need charts.
- Chart types available: bar, line, pie, donut, scatter, stacked_bar, multiline.
- Datasets must contain only plain numbers in the data array — no strings.

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
