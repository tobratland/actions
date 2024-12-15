      
import os
import sys
import json
import requests
from github import Github
from git import Git, Repo
import re
import tiktoken
import ast  # For more accurate Python parsing
from functools import lru_cache

# --- Constants ---
MAX_TOKEN_COUNT = 6000  # Adjustable, consider making it an input
ENCODING_MODEL = "gpt-4o-mini"  # Adjustable
FUNCTION_DEF_TOKEN_LIMIT = 2000  # Limit for function definitions
CACHE_SIZE = 128  # For LRU caching


MAX_TOKEN_COUNT = int(os.getenv("MAX_TOKEN_COUNT", MAX_TOKEN_COUNT))


# --- Helper Functions ---

def get_contextual_files(repo_path):
    manual_content = ""
    example_contents = ""

    repo_path = os.environ.get("GITHUB_WORKSPACE", "/github/workspace")

    # Read developer manual
    manual_path = os.path.join(repo_path, "developer_manual.md")
    if os.path.exists(manual_path):
        with open(manual_path, "r") as f:
            manual_content = f.read()
    else:
        print("[DEBUG] No developer_manual.md found.")

    # Read example files
    examples_path = os.path.join(repo_path, "examples")
    if os.path.exists(examples_path):
        for root, dirs, files in os.walk(examples_path):
            for file in files:
                with open(os.path.join(root, file), "r") as f:
                    content = f.read()
                    example_contents += f"\n### Example File: {file}\n{content}"
    else:
        print("[DEBUG] No examples directory found.")

    return manual_content, example_contents

def get_changed_files(repo, base_branch, head_branch, file_extensions):
    origin = repo.remotes.origin
    print("[DEBUG] Fetching all branches...")
    origin.fetch()

    if base_branch not in repo.heads:
        print(f"[DEBUG] Base branch {base_branch} not found locally. Creating it.")
        repo.create_head(base_branch, origin.refs[base_branch])
    else:
        repo.heads[base_branch].set_tracking_branch(origin.refs[base_branch])

    print(f"[DEBUG] Checking out head branch: {head_branch}")
    repo.git.checkout(head_branch)

    base_commit = repo.merge_base(base_branch, head_branch)
    if not base_commit:
        raise Exception(
            f"Could not find common ancestor between {base_branch} and {head_branch}"
        )

    print(f"[DEBUG] Found common ancestor: {base_commit[0].hexsha}")

    diff_index = base_commit[0].diff(head_branch, create_patch=True)

    print("[DEBUG] Filtering diffs by file extensions:", file_extensions)
    filtered_diffs = []
    for diff in diff_index:
        if diff.a_path:
            file_path = diff.a_path
        else:
            file_path = diff.b_path

        if any(file_path.endswith(ext) for ext in file_extensions):
            print(f"[DEBUG] Including diff for file: {file_path}")
            filtered_diffs.append(diff)
        else:
            print(f"[DEBUG] Skipping file not matching extensions: {file_path}")

    return filtered_diffs

def get_issue_content(repo, issue_number):
    """Fetches the title and body of a given issue."""
    try:
        issue = repo.get_issue(number=issue_number)
        return issue.title, issue.body
    except Exception as e:
        print(f"[DEBUG] Error fetching issue #{issue_number}: {e}")
        return None, None

@lru_cache(maxsize=CACHE_SIZE)
def get_function_definitions(repo_path, file_extension, function_name):
    """
    Finds function definitions in the repository using AST for Python, and falls back to regex for others.
    Caches results using @lru_cache.
    """
    function_definitions = ""
    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.endswith(file_extension):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, "r") as f:
                        content = f.read()
                        if file_extension == ".py":
                            function_definitions += extract_python_function_def(
                                content, function_name, file
                            )
                        else:
                            function_definitions += extract_function_def_regex(
                                content, function_name, file
                            )
                except Exception as e:
                    print(f"[DEBUG] Error reading file {filepath}: {e}")
    return function_definitions

def extract_python_function_def(content, function_name, filename):
    """Extracts a Python function definition using the ast module."""
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    # Get the function definition's source code
                    func_def = ast.get_source_segment(content, node)
                    num_tokens = num_tokens_from_string(func_def, ENCODING_MODEL)

                    if num_tokens <= FUNCTION_DEF_TOKEN_LIMIT:
                        return (
                            f"\n\n--- Function Definition (File: {filename}) ---\n"
                            + func_def
                        )
                    else:
                        print(
                            f"[DEBUG] Function {function_name} in {filename} exceeds token limit. Skipping."
                        )
                        return ""
    except SyntaxError as e:
        print(f"[DEBUG] Syntax error in {filename}: {e}")
        return ""
    return ""

def extract_function_def_regex(content, function_name, filename):
    """
    Extracts function definition using regex (fallback for non-Python files).
    """
    pattern = rf"^(def|function)\s+{function_name}\s*\(.*\).*{{"  # Python, JavaScript
    match = re.search(pattern, content, re.MULTILINE)
    if match:
        start = match.start()
        end = content.find("\n\n", start)  # Look for the next empty line
        if end == -1:
            end = len(content)
        func_def = content[start:end]
        num_tokens = num_tokens_from_string(func_def, ENCODING_MODEL)
        if num_tokens <= FUNCTION_DEF_TOKEN_LIMIT:
            return f"\n\n--- Function Definition (File: {filename}) ---\n" + func_def
        else:
            print(
                f"[DEBUG] Function {function_name} in {filename} exceeds token limit. Skipping."
            )
            return ""
    return ""

def get_called_functions(diff_content):
    """
    Extracts function names that are called in the diff content.
    """
    called_functions = set()
    for line in diff_content.split("\n"):
        if line.startswith("+"):
            matches = re.findall(r"(\w+)\s*\(", line)
            for match in matches:
                called_functions.add(match)
    return called_functions

def num_tokens_from_string(string: str, encoding_name: str) -> int:
    """Returns the number of tokens in a text string."""
    try:
        encoding = tiktoken.encoding_for_model(encoding_name)
    except KeyError:
        print(f"[DEBUG] Model {encoding_name} not found. Defaulting to cl100k_base.")
        encoding = tiktoken.get_encoding("cl100k_base")
    num_tokens = len(encoding.encode(string))
    return num_tokens

def split_diff_into_chunks(diff_content, max_tokens):
    """Splits the diff content into chunks that are within the token limit."""
    chunks = []
    current_chunk = ""
    for line in diff_content.split("\n"):
        if (
            num_tokens_from_string(current_chunk + line, ENCODING_MODEL)
            > max_tokens
        ):
            chunks.append(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
    chunks.append(current_chunk)
    return chunks

def review_code_with_llm(
    filename,
    diff_content,
    manual_content,
    example_contents,
    issue_content,
    function_definitions,
    api_key,
):
    """
    Reviews a code diff using an LLM, with handling for large files.
    """
    # Add line numbers to diff content
    numbered_diff = []
    current_line = 0
    for line in diff_content.split("\n"):
        if line.startswith("@@"):
            # Parse the @@ line to get new starting line number
            match = re.search(r"\+(\d+)", line)
            if match:
                current_line = int(match.group(1)) - 1
            numbered_diff.append(line)
        elif line.startswith("+"):
            current_line += 1
            numbered_diff.append(f"{current_line:4d} | {line}")
        elif line.startswith("-"):
            numbered_diff.append(line)
        else:
            current_line += 1
            numbered_diff.append(f"{current_line:4d} | {line}")

    numbered_diff_content = "\n".join(numbered_diff)

    diff_chunks = split_diff_into_chunks(numbered_diff_content, MAX_TOKEN_COUNT)
    all_feedback = []

    for chunk_index, diff_chunk in enumerate(diff_chunks):
        prompt = f"""
    You are a code reviewer. Base your review on best practices for safe and efficient code. Give examples where applicable, and reference the developer manual or examples, if they exist, for more information.
    Your code review should be actionable and provide clear feedback to the developer, including suggestions for improvement.
    **Provide in-line code suggestions where appropriate using the following format:**
    ```suggestion
    # Your suggested code here

        

    Use code with caution.Python

    Task Context (if applicable):
    {issue_content}

    Below is the developer manual and examples (they may be empty):

    Developer Manual:
    {manual_content}

    Examples:
    {example_contents}

    Function Definitions (if found):
    {function_definitions}

    Now, review the following code diff. Line numbers are shown at the start of each line:

    File: {filename} (Chunk {chunk_index + 1} of {len(diff_chunks)})
    Diff:
    {diff_chunk}

    Provide your feedback in the following JSON format:
    {{
    "filename": "{filename}",
    "chunk": {chunk_index + 1},
    "comments": [
    {{
    "line": integer, # Use the line numbers shown in the diff
    "comment": "string"
    }},
    ...
    ]
    }}
    """

        
    headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload,
    )

    try:
        response_json = response.json()
        all_feedback.append(response_json)
    except Exception as e:
        print(f"[DEBUG] Error parsing LLM response: {e}")
        print(f"[DEBUG] Response content: {response.text}")

    return all_feedback


def get_position_in_diff(diff, target_line):
    print("[DEBUG] Calculating position in diff for target_line:", target_line)
    diff_lines = diff.split("\n")
    position = None
    current_line = None
    current_position = 0

        
    for line in diff_lines:
        # Reset position counting at each hunk header
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                current_line = int(match.group(1)) - 1
                current_position = 0
            continue

        current_position += 1

        if line.startswith("+"):
            current_line += 1
            if current_line == target_line:
                position = current_position
                break
        elif line.startswith("-"):
            # Don't increment current_line for removed lines
            continue
        else:
            # Context line
            current_line += 1

    if position is not None:
        print(f"[DEBUG] Found position {position} for line {target_line}")
        # Print surrounding context for verification
        start_idx = max(0, diff_lines.index(line) - 2)
        end_idx = min(len(diff_lines), diff_lines.index(line) + 3)
        print("[DEBUG] Context around found position:")
        for i in range(start_idx, end_idx):
            print(f"  {diff_lines[i]}")
    else:
        print(f"[DEBUG] Could not find position for line {target_line}")

    return position

    
def post_comments(
comments, diffs, repo_full_name, pr_number, commit_id, github_token
):
    from github import GithubException

        
    g = Github(github_token)
    repo = g.get_repo(repo_full_name)

    # Re-fetch the PR to ensure we have the latest head commit
    pull_request = repo.get_pull(pr_number)
    commit_id = pull_request.head.sha
    commit = repo.get_commit(commit_id)
    print("[DEBUG] Refreshed commit_id to match pr.head.sha:", commit_id)

    print("[DEBUG] Posting comments...")
    print(
        f"[DEBUG] PR Number: {pr_number}, commit_id: {commit_id}, repo_full_name: {repo_full_name}"
    )
    print("[DEBUG] pr.head.sha:", pull_request.head.sha)

    for comment in comments:
        filename = comment["filename"]
        line = comment["line"]
        body = comment["comment"]

        print(f"[DEBUG] Attempting to post comment on {filename} at line {line}")
        file_diff = diffs.get(filename)
        if not file_diff:
            print(f"[DEBUG] No diff found for {filename}, skipping this comment.")
            continue

        position = get_position_in_diff(file_diff, line)
        print(f"[DEBUG] Computed position for file {filename}, line {line}: {position}")

        if position is None:
            print(
                f"[DEBUG] Could not find position for file {filename} line {line}, skipping this comment."
            )
            continue

        # Show diff context around the computed position for debugging
        diff_lines = file_diff.split("\n")
        start_context = max(0, position - 5)
        end_context = min(len(diff_lines), position + 5)
        print("[DEBUG] Diff context around computed position:")
        for c_idx in range(start_context, end_context):
            print(f"   {c_idx}: {diff_lines[c_idx]}")

        try:
            pull_request.create_review_comment(
                body=body, commit_id=commit, path=filename, position=position
            )
            print(f"Comment posted successfully on {filename} line {line}")
        except GithubException as e:
            print(f"GitHub API Error: {e.status}, {e.data}")
        except Exception as e:
            print("[DEBUG] Generic exception encountered while posting comment")
            print("[DEBUG] Exception type:", type(e))
            print("[DEBUG] Exception content:", str(e))
            # Not exiting here, continuing to next comment

    
def main():
    try:
        print("[DEBUG] Starting code review action...")
        openai_api_key = sys.argv[1]
        file_types_input = sys.argv[2] if len(sys.argv) > 2 else ""
        github_token = os.environ.get("GITHUB_TOKEN")
            
        if file_types_input:
                file_extensions = [ext.strip() for ext in file_types_input.split(",")]
        else:
            file_extensions = []
        print("[DEBUG] File extensions:", file_extensions)

        g = Github(github_token)
        repo_full_name = os.environ["GITHUB_REPOSITORY"]
        print("[DEBUG] Repo Full Name:", repo_full_name)
        repo = g.get_repo(repo_full_name)

        event_path = os.environ["GITHUB_EVENT_PATH"]
        print("[DEBUG] Reading event from:", event_path)
        with open(event_path, "r") as f:
            event = json.load(f)

        pr_number = event["pull_request"]["number"]
        pr = repo.get_pull(pr_number)
        base_branch = pr.base.ref
        head_branch = pr.head.ref
        commit_id = pr.head.sha

        print(
            f"[DEBUG] PR Number: {pr_number}, Base Branch: {base_branch}, Head Branch: {head_branch}, Commit ID: {commit_id}"
        )

        repo_path = "/github/workspace"
        os.chdir(repo_path)

        git_cmd = Git(repo_path)
        git_cmd.config("--global", "--add", "safe.directory", repo_path)

        repo_git = Repo(repo_path)
        diffs = get_changed_files(repo_git, base_branch, head_branch, file_extensions)

        manual_content, example_contents = get_contextual_files(repo_path)

        # --- Get Issue Context ---
        issue_title, issue_body = None, None
        match = re.search(r"closes\s*#(\d+)", pr.body, re.IGNORECASE)
        if match:
            issue_number = int(match.group(1))
            issue_title, issue_body = get_issue_content(repo, issue_number)
            print(f"[DEBUG] Found related issue: #{issue_number}")

        issue_content = ""
        if issue_title:
            issue_content += f"Issue Title: {issue_title}\n"
        if issue_body:
            issue_content += f"Issue Body: {issue_body}\n"

        # --- Process Diffs ---
        all_comments = []
        diffs_by_file = {}
        for diff in diffs:
            if diff.a_path:
                filename = diff.a_path
            else:
                filename = diff.b_path

            print(f"Reviewing {filename}...")
            diff_content = diff.diff.decode("utf-8", errors="replace")
            diffs_by_file[filename] = diff_content

            # --- Get Function Definitions ---
            called_functions = get_called_functions(diff_content)
            function_definitions = ""
            for function_name in called_functions:
                for file_extension in file_extensions:
                    function_definitions += get_function_definitions(
                        repo_path, file_extension, function_name
                    )

            llm_responses = review_code_with_llm(
                filename=filename,
                diff_content=diff_content,
                manual_content=manual_content,
                example_contents=example_contents,
                issue_content=issue_content,
                function_definitions=function_definitions,
                api_key=openai_api_key,
            )

            print("[DEBUG] LLM responses:", llm_responses)

            for llm_response in llm_responses:
                try:
                    llm_text = llm_response["choices"][0]["message"]["content"]
                    json_start = llm_text.find("{")
                    json_end = llm_text.rfind("}") + 1
                    llm_json_str = llm_text[json_start:json_end]
                    feedback = json.loads(llm_json_str)

                    print("[DEBUG] Parsed feedback JSON:", feedback)

                    comments = feedback.get("comments", [])
                    for c in comments:
                        c["filename"] = filename
                    all_comments.extend(comments)

                except Exception as e:
                    print(f"Error parsing LLM response for {filename}: {e}")
                    if "llm_text" in locals():
                        print("[DEBUG] LLM text that failed to parse:", llm_text)
                    continue

        post_comments(
            comments=all_comments,
            diffs=diffs_by_file,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            commit_id=commit_id,
            github_token=github_token,
        )
        print(f"[DEBUG] PR head commit SHA: {pr.head.sha}")
        print(f"[DEBUG] Using commit_id: {commit_id}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()