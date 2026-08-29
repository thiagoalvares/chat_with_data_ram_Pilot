def build_code_gen_prompt(schemas: dict, labels: dict, user_question: str, join_hints: list = None,
                          error_feedback: str = None, history: list = None, prev_result_meta: str = None) -> list:
    """
    Build code generation prompt for multi-file linking scenarios.

    Args:
        schemas: Dict of {slot: schema_str} e.g. {"a": schema, "b": schema, "c": schema}
        labels: Dict of {slot: label} e.g. {"a": "Customers", "b": "Sales", "c": "Products"}
        user_question: The user's question
        join_hints: List of accepted join suggestions (from UI or natural language parsing)
        error_feedback: Error from previous attempt (for retry)
        history: Last Q&A pair for context
        prev_result_meta: Previous query result metadata
    """
    # Build DataFrames list dynamically
    df_list = []
    schema_text = []
    for slot in sorted(schemas.keys()):
        df_name = f"df_{slot}"
        label = labels[slot]
        schema = schemas[slot]
        df_list.append(f"- {df_name}: \"{label}\" — already loaded")
        schema_text.append(f"DataFrame {slot.upper()} — \"{label}\" schema:\n{schema}")

    df_section = "\n".join(df_list)
    schema_section = "\n\n".join(schema_text)

    # Build join hints section
    join_hints_text = ""
    if join_hints:
        hints_list = []
        for hint in join_hints:
            hints_list.append(
                f"- Link df_{hint['slot1']}.{hint['column1']} to df_{hint['slot2']}.{hint['column2']} "
                f"(User accepted: {hint['file1']} ↔ {hint['file2']})"
            )
        join_hints_text = "\n\nUser-accepted join suggestions:\n" + "\n".join(hints_list)

    system = f"""You are a Python/Pandas data analyst specializing in multi-file data linking and integration.
You will receive multiple DataFrame schemas and a user question.
Your ONLY job is to write a single Python code snippet that links, merges, or analyzes the DataFrames and stores the final answer in a variable called `result`.

The DataFrames are:
{df_section}
{join_hints_text}

Rules:
- All DataFrames are already loaded. Do NOT reload or re-read any files.
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

CRITICAL - Result Size Management:
- ALWAYS limit your final result to a reasonable size (max 10,000 rows).
- If merging produces a large result, use .head(10000) or filter to top N records BEFORE assigning to `result`.
- For table display requests, NEVER return the full merged dataset — summarize or filter first.
- Example: result = merged_df.groupby('Project').agg({{'Value': 'sum'}}).head(100)  # Summary, not raw merge

Data cleaning rules — always apply before modeling:
- Convert date columns using pd.to_datetime(df[col], errors='coerce') before any date operations.
- Convert Categorical columns before math or encoding: df[col] = df[col].astype(str)
- Convert currency/accounting columns before math: strip $, commas, convert (76985) to -76985.
- Use pd.to_numeric(df[col], errors='coerce') to safely convert any column to numeric.
- Drop or fill NaN values before fitting any model: df.dropna(subset=[cols]) or df[col].fillna(0).
- Validate the python code is correct.
- Apply the same cleaning steps to ALL DataFrames before linking them.

Linking and join rules — core analytical patterns:
- **Join key identification**: Look for columns with matching names OR user-accepted join hints. Common join keys: ID fields, account numbers, customer IDs, product codes, dates.
- **Merge syntax**: df_merged = pd.merge(df_a, df_b, left_on='col1', right_on='col2', how='inner')
- **Join types**:
  - inner: Keep only matching rows (default for analysis)
  - left: Keep all rows from left DataFrame, fill nulls for non-matches
  - outer: Keep all rows from both DataFrames (useful for finding gaps)
- **Multi-file joins**: Chain merges sequentially:
  ```python
  df_merged = pd.merge(df_a, df_b, on='common_key', how='inner')
  df_merged = pd.merge(df_merged, df_c, on='another_key', how='left')
  ```
- **Handle duplicate columns**: Use suffixes to distinguish: suffixes=('_file1', '_file2')
- **Validation after merge**: CRITICAL — After each merge, immediately check the result size:
  ```python
  df_merged = pd.merge(df_a, df_b, on='key')
  if len(df_merged) > len(df_a) * 10:  # Explosion detected
      # Use inner join or check for duplicate keys
      df_merged = pd.merge(df_a.drop_duplicates('key'), df_b.drop_duplicates('key'), on='key', how='inner')
  ```
- **Avoid cartesian products**: NEVER merge on columns with many duplicate values unless that's explicitly required. If merging creates billions of rows, you're doing it wrong — use groupby + aggregation first.
- **Data type matching**: ALWAYS ensure join keys have the same data type before merging:
  ```python
  df_a['key'] = df_a['key'].astype(str)  # Convert to string
  df_b['key'] = df_b['key'].astype(str)
  ```
- **Attribution**: When computing summary statistics, identify which file each data point came from. Add a source column if needed.
- **Missing join keys**: If no obvious join key exists, ask user or use string fuzzy matching (difflib) to find potential matches.

Error handling and user-friendly messages:
- **Join explosion detected**: If after merging the result has 10x+ more rows than expected, set result to:
  "The merge created an unexpectedly large result (possible cartesian product). This usually means the join column has duplicate values. Try: Remove duplicates from the join columns before merging, or verify you're joining on the correct key column."
- **No matching records**: If after merging the result is empty, set result to:
  "No rows matched between the files on the specified join column. Possible reasons: (1) The join columns have different data types (e.g., one is text, one is numeric), (2) No values exist in both files, (3) The column names are different than expected. Try: Check your data, ensure data types match, or use 'Link ColumnA to ColumnB' syntax to specify the exact columns."
- **Data type mismatch**: If you detect type incompatibility before merging (e.g., one column is datetime, the other is string), convert both to the same type OR set result to:
  "Cannot merge: the join columns have incompatible data types. File 1 has [type1] and File 2 has [type2]. Converting both to string format for comparison."
- **Ambiguous file selection**: If the question doesn't clearly indicate which files to use (e.g., "compare these files" when 3+ are uploaded), prioritize the most recently uploaded files or set result to:
  "Your question references multiple files but doesn't specify which ones to compare. I used [File X and File Y]. If you meant different files, please specify explicitly (e.g., 'Compare File 1 to File 3')."

Heterogeneous schema handling:
- Files may have completely different structures — analyze the question to determine which columns from each file are needed.
- Don't assume all files need to be merged together — sometimes only 2 of 4 files are relevant.
- If files have no common keys but the question requires linking them, use cross join with caution: df1.assign(key=1).merge(df2.assign(key=1), on='key').drop('key', axis=1)

Variance vs Linking detection:
- **Variance analysis**: Same schema, different time periods → focus on row-by-row comparison (variance = df_b_col - df_a_col)
- **Linking analysis**: Different schemas, different entities → focus on JOIN operations to combine data

Machine learning rules (same as variance mode):
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

    user_content = schema_section + f"\n\nUser question: {user_question}"

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
                            labels: dict, history: list = None, metadata: dict = None) -> list:
    """
    Build answer generation prompt for multi-file linking results.

    Args:
        user_question: The user's question
        query_result: String representation of the result
        labels: Dict of {slot: label} e.g. {"a": "Customers", "b": "Sales"}
        history: Last Q&A pair for context
        metadata: Additional metadata (column names, dtypes)
    """
    # Build file list for attribution
    file_list = ", ".join([f"\"{labels[slot]}\"" for slot in sorted(labels.keys())])

    system = f"""You are a helpful data analyst assistant specializing in multi-file data linking and integration.
You are working with the following datasets: {file_list}.

CRITICAL INSTRUCTION: You MUST respond with a valid JSON object only. No exceptions. No markdown fences. No text before or after the JSON.

Your JSON response must have these exact keys:
{{
  "answer": "A clear, well-written answer to the user's question in 2-4 sentences. Use business language, not technical jargon.",
  "chart": null OR {{
    "type": "bar|line|pie",
    "title": "Chart title",
    "labels": ["label1", "label2", ...],
    "datasets": [
      {{
        "label": "Series name",
        "data": [number1, number2, ...]
      }}
    ]
  }},
  "formatting": OPTIONAL - only include when user requests highlighting (see formatting rules below)
}}

Guidelines for your answer:
- **Table formatting**: If the result is tabular (multiple rows/columns), format it as a markdown table using | pipes |. This is CRITICAL for readability.
- **Attribution**: ALWAYS specify which file each number or fact comes from. Example: "Based on {labels.get('a', 'File 1')}, the total was $1.2M, while {labels.get('b', 'File 2')} shows $1.5M."
- **Join statistics**: If files were merged, mention the join result (e.g., "Linking {labels.get('a', 'File 1')} to {labels.get('b', 'File 2')} matched 95% of records (1,234 of 1,300).")
- **Precision**: Round numbers appropriately. Use K for thousands (1.2K), M for millions (5.3M).
- **Context**: If the result has multiple rows or columns, summarize the key insights. Don't list every row.
- **Clarity**: Write for a business user, not a data scientist. Avoid technical terms like "merge", "left join", "DataFrame".
- **Completeness**: Answer the full question. If they asked for "top 5", provide all 5. If they asked "why", explain the why.

Chart rules:
- Only provide a chart if it would add value to the answer (e.g., trends, comparisons, distributions).
- For comparisons, use bar charts. For trends over time, use line charts. For part-of-whole, use pie charts.
- Limit labels/datasets to 10-15 items max (use "Top 10" if needed).
- Chart data must match the numbers in your written answer exactly.
- If no chart is appropriate, set "chart": null.

Optional formatting field (include ONLY when user explicitly requests highlighting/coloring):
- Omit the "formatting" field entirely unless user mentions: "highlight", "color", "red", "yellow", "green", "warn", "flag", etc.
- When user requests highlighting, add a "formatting" field with color rules.
- Format: {{"formatting": {{"rules": [...], "row_level": true/false}}}}
- Column names must match EXACTLY as they appear in the data result.
- Maximum 10 rules to avoid complexity.

Rule types supported:

1. NUMERIC CONDITIONS:
   {{"column": "TotalAmount", "condition": "<|>|<=|>=|==|!=", "value": 10000, "color": "red"}}

2. TEXT CONDITIONS:
   {{"column": "Owner", "condition": "==|!=|contains|startswith|endswith", "value": "Thiago Alvares", "color": "yellow"}}
   - Use "==" for exact match (case-insensitive)
   - Use "contains" for substring search

3. NULL/EMPTY:
   {{"column": "Email", "condition": "is_null", "color": "red"}}
   {{"column": "Description", "condition": "is_not_null", "color": "green"}}

4. DATE CONDITIONS:
   {{"column": "DueDate", "condition": "<|>|<=|>=|==", "value": "2024-01-01", "color": "red"}}
   - Dates as YYYY-MM-DD strings
   - Supports: before (<), after (>), on (==)

5. TOP/BOTTOM N:
   {{"column": "Revenue", "condition": "top_n", "value": 5, "color": "green"}}
   {{"column": "Score", "condition": "bottom_n", "value": 3, "color": "red"}}

6. CROSS-COLUMN:
   {{"column": "Actual", "condition": ">", "compare_column": "Budget", "color": "red"}}
   - Compares two columns: Actual > Budget

7. MULTIPLE CONDITIONS (AND):
   {{"conditions": [
     {{"column": "Status", "condition": "==", "value": "Failed"}},
     {{"column": "Priority", "condition": "==", "value": "High"}}
   ], "operator": "and", "color": "red"}}

Row-level highlighting:
- Set "row_level": true to highlight entire row instead of just the cell
- Example: {{"formatting": {{"rules": [...], "row_level": true}}}}

Colors: "red", "yellow", "green" (Excel standard)

Formatting examples:
{{"answer": "...", "chart": null, "formatting": {{"rules": [{{"column": "TotalAmount", "condition": ">", "value": 10000, "color": "yellow"}}], "row_level": true}}}}
{{"answer": "...", "chart": null, "formatting": {{"rules": [{{"column": "Sales", "condition": "<", "value": 1000000, "color": "red"}}]}}}}

If user does not request highlighting, omit "formatting" entirely - just return: {{"answer": "...", "chart": null}}

Quality checks:
- Read the query result carefully before writing your answer.
- If the result is empty or null, say so clearly (e.g., "No matching records were found").
- If the result is an error message, explain it in plain language.
- Ensure your JSON is syntactically valid (matching quotes, brackets, commas).

Examples of good answers:
- "Linking Customers to Sales, we found 1,234 customer records with purchases (95% match rate). Total revenue was $5.3M, with the top customer contributing $450K (8.5%)."
- "Comparing Budget ({labels.get('a', 'File 1')}) to Actuals ({labels.get('b', 'File 2')}), Marketing spent $120K vs a budget of $100K, a $20K (20%) overage."
- "Products shows 45 items, but only 38 had sales in Orders. The 7 unsold items were all added in Q4 2024."

Return ONLY a valid JSON object. Nothing before or after."""

    user_content = f"""User question: {user_question}

Query result:
{query_result}"""

    if metadata:
        user_content += f"\n\nResult metadata: {metadata}"

    messages = [{"role": "system", "content": system}]

    # Include last Q&A pair for context
    if history and len(history) >= 2:
        messages.append(history[-2])  # Last user question
        messages.append(history[-1])  # Last assistant answer

    messages.append({"role": "user", "content": user_content})

    return messages
