import os
import sys
import json
import requests
from github import Github
from git import Git, Repo
import re
import tiktoken
import ast
from functools import lru_cache

# --- Constants ---
MAX_TOKEN_COUNT = 150000  # adjustable
ENCODING_MODEL = "gpt-4o-mini"
FUNCTION_DEF_TOKEN_LIMIT = 5000
CACHE_SIZE = 128

MAX_TOKEN_COUNT = int(os.getenv("MAX_TOKEN_COUNT", MAX_TOKEN_COUNT))

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
    diffs_by_file = {}
    for diff in diff_index:
        file_path = diff.a_path if diff.a_path else diff.b_path
        if any(file_path.endswith(ext) for ext in file_extensions):
            print(f"[DEBUG] Including diff for file: {file_path}")
            try:
                if hasattr(diff, 'diff'):
                    if isinstance(diff.diff, str):
                        diff_content = diff.diff
                    else:
                        diff_content = diff.diff.decode('utf-8', errors='replace')
                else:
                    diff_content = repo.git.diff(
                        base_commit[0].hexsha,
                        head_branch,
                        '--',
                        file_path,
                        encoding='utf-8'
                    )
                print(f"[DEBUG] Successfully processed diff content for {file_path}")
                filtered_diffs.append(diff)
                diffs_by_file[file_path] = diff_content
            except Exception as e:
                print(f"[DEBUG] Error processing diff for {file_path}: {str(e)}")
        else:
            print(f"[DEBUG] Skipping file not matching extensions: {file_path}")
    return filtered_diffs, diffs_by_file


def get_issue_content(repo, issue_number):
    try:
        issue = repo.get_issue(number=issue_number)
        return issue.title, issue.body
    except Exception as e:
        print(f"[DEBUG] Could not fetch issue #{issue_number}: {e}")
        return None, None


@lru_cache(maxsize=CACHE_SIZE)
def get_function_definitions(repo_path, file_extension, function_name):
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

def num_tokens_from_string(string: str, encoding_name: str) -> int:
    try:
        encoding = tiktoken.encoding_for_model(encoding_name)
    except KeyError:
        print(f"[DEBUG] Model {encoding_name} not found. Defaulting to cl100k_base.")
        encoding = tiktoken.get_encoding("cl100k_base")
    num_tokens = len(encoding.encode(string))
    return num_tokens

def extract_python_function_def(content, function_name, filename):
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
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
    pattern = rf"^(def|function)\s+{function_name}\s*\(.*\).*{{"
    match = re.search(pattern, content, re.MULTILINE)
    if match:
        start = match.start()
        end = content.find("\n\n", start)
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
    print(f"[DEBUG] get_called_functions input type: {type(diff_content)}")
    if not isinstance(diff_content, str):
        try:
            diff_content = diff_content.decode('utf-8', errors='replace')
        except Exception:
            diff_content = str(diff_content)
    
    called_functions = set()
    try:
        for line in diff_content.split("\n"):
            if line.startswith("+"):
                matches = re.findall(r"(\w+)\s*\(", line)
                called_functions.update(matches)
    except Exception as e:
        print(f"[DEBUG] Error parsing diff content: {str(e)}")
    return called_functions

def split_diff_into_chunks(diff_content, max_tokens):
    chunks = []
    current_chunk = ""
    for line in diff_content.split("\n"):
        if num_tokens_from_string(current_chunk + line, ENCODING_MODEL) > max_tokens:
            chunks.append(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
    chunks.append(current_chunk)
    return chunks


def number_and_combine_diffs(diffs_by_file):
    combined_output = []
    line_maps = {}
    for filename, diff_content in diffs_by_file.items():
        combined_output.append(f"--- File: {filename} ---")
        lines = diff_content.split("\n")
        display_line_num = 0
        position_num = 0
        line_maps[filename] = {}
        
        for line in lines:
            if line.startswith("@@"):
                combined_output.append(line)
            else:
                display_line_num += 1
                combined_output.append(f"{display_line_num:4d} | {line}")
                
                if line.startswith("+") or (line.startswith(" ") or (not line.startswith("@") and not line.startswith("-") and not line.startswith("+"))):
                    position_num += 1
                    line_maps[filename][display_line_num] = position_num
    return "\n".join(combined_output), line_maps


def get_position_in_diff(filename, target_line, line_maps):
    if filename not in line_maps:
        print(f"[DEBUG] No line map found for {filename}")
        return None
    pos = line_maps[filename].get(target_line)
    if pos is None:
        print(f"[DEBUG] No position found for {filename} line {target_line}")
    return pos


def post_comments(comments, diffs, repo_full_name, pr_number, commit_id, github_token, line_maps):
    from github import GithubException

    g = Github(github_token)
    repo = g.get_repo(repo_full_name)

    pull_request = repo.get_pull(pr_number)
    commit_id = pull_request.head.sha
    commit = repo.get_commit(commit_id)
    print("[DEBUG] Refreshed commit_id to match pr.head.sha:", commit_id)

    print("[DEBUG] Posting inline comments...")
    print(
        f"[DEBUG] PR Number: {pr_number}, commit_id: {commit_id}, repo_full_name: {repo_full_name}"
    )
    print("[DEBUG] pr.head.sha:", pull_request.head.sha)

    for comment in comments:
        filename = comment["filename"]
        line = comment["line"]
        body = comment["comment"]

        print(f"[DEBUG] Attempting to post inline comment on {filename} at line {line}")
        file_diff = diffs.get(filename)
        if not file_diff:
            print(f"[DEBUG] No diff found for {filename}, skipping this comment.")
            continue

        position = get_position_in_diff(filename, line, line_maps)
        print(f"[DEBUG] Computed position for file {filename}, line {line}: {position}")

        if position is None:
            print(
                f"[DEBUG] Could not find position for file {filename} line {line}, skipping this comment."
            )
            continue

        try:
            pull_request.create_review_comment(
                body=body, commit_id=commit, path=filename, position=position
            )
            print(f"Comment posted successfully on {filename} line {line}")
        except GithubException as e:
            print(f"GitHub API Error: {e.status}, {e.data}")
        except Exception as e:
            print("[DEBUG] Generic exception encountered while posting inline comment")
            print("[DEBUG] Exception type:", type(e))
            print("[DEBUG] Exception content:", str(e))


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
        diffs, diffs_by_file = get_changed_files(
            repo_git, base_branch, head_branch, file_extensions
        )

        manual_content, example_contents = get_contextual_files(repo_path)

        # Find related issue
        issue_number = None
        if pr.body is not None:
            match = re.search(r"closes\s*#(\d+)", pr.body, re.IGNORECASE)
            if match:
                issue_number = int(match.group(1))

        pr_body = pr.body or ""

        if issue_number:
            issue_title, issue_body = get_issue_content(repo, issue_number)
            if issue_title or issue_body:
                issue_content = f"Issue Title: {issue_title}\nIssue Body: {issue_body}\nPR Body: {pr_body}\n"
            else:
                print("[DEBUG] Issue found but could not fetch details.")
                issue_content = f"Related issue found but could not fetch details.\nPR Body: {pr_body}\n"
        else:
            print("[DEBUG] No related issue found in PR description.")
            issue_content = f"No related issue found.\nPR Body: {pr_body}\n"

        # Combine all diffs with per-file numbering
        combined_diff, line_maps = number_and_combine_diffs(diffs_by_file)

        # Extract called functions from combined diff
        called_functions = get_called_functions(combined_diff)
        function_definitions = ""
        for function_name in called_functions:
            for file_extension in file_extensions:
                function_definitions += get_function_definitions(
                    repo_path, file_extension, function_name
                )

            prompt_template = """You are a senior code reviewer. Base your review on best practices for safe and efficient code. Give examples where applicable, and reference the developer manual or examples, if they exist, for more information.
    Its important that the feedback is actionable and clear.
    If there are any issues with the code, provide a clear and concise explanation of the problem, and suggest a solution. 
    If there is a github issue connected to the code, it is the top priority that the github issue is completed by the pull request, if not you must provide feedback on what is missing to complete it, and if possible a clear path forward. if a complete solution to all the parts of the issue is included, provide a single comment that says that the issue is solved.

    Provide in-line code suggestions where appropriate using the following format:
    ```suggestion
    # Your suggested code here
    {ISSUE_CONTENT}

    {MANUAL_CONTENT}

    {EXAMPLES_CONTENT}

    {FUNCTION_DEFINITIONS}

    Below are the combined diffs of all changed files. Each file has its own heading and line numbers start at 1 for that file.

    {DIFF_CONTENT}

    Provide feedback in JSON format: { "comments": [ { "filename": "string", "line": integer, "comment": "string" } ], "issue_comment": [ { "issue": "string", "comment": "string", "how_is_the_issue_solved": "string" } ] }"""


        # Insert dynamic contents
        prompt = prompt_template.replace("{ISSUE_CONTENT}", issue_content)
        prompt = prompt.replace("{MANUAL_CONTENT}", manual_content)
        prompt = prompt.replace("{EXAMPLES_CONTENT}", example_contents)
        prompt = prompt.replace("{FUNCTION_DEFINITIONS}", function_definitions)
        prompt = prompt.replace("{DIFF_CONTENT}", combined_diff)

        # Log prompt
        with open("/github/workspace/prompt_logs.txt", "a") as log_file:
            log_file.write("\n--- Combined Prompt ---\n")
            log_file.write(prompt + "\n")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {openai_api_key}",
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
        )

        all_feedback = []
        try:
            response_json = response.json()
            all_feedback.append(response_json)
        except Exception as e:
            print(f"[DEBUG] Error parsing LLM response: {e}")
            print(f"[DEBUG] Response content: {response.text}")

        all_comments = []
        issue_comments = []
        for llm_response in all_feedback:
            try:
                llm_text = llm_response["choices"][0]["message"]["content"]
                json_start = llm_text.find("{")
                json_end = llm_text.rfind("}") + 1
                llm_json_str = llm_text[json_start:json_end]
                feedback = json.loads(llm_json_str)
                print("[DEBUG] Parsed feedback JSON:", feedback)

                comments = feedback.get("comments", [])
                for c in comments:
                    if "filename" not in c:
                        c["filename"] = "unknown_file"
                all_comments.extend(comments)

                # Extract issue-level comments
                issue_comments.extend(feedback.get("issue_comment", []))

            except Exception as e:
                print(f"Error parsing LLM response: {e}")
                if "llm_text" in locals():
                    print("[DEBUG] LLM text that failed to parse:", llm_text)
                continue

        # Post top-level issue comments (if any)
        pull_request = repo.get_pull(pr_number)
        for ic in issue_comments:
            issue_comment_body = ic.get("comment", "")
            if issue_comment_body.strip():
                print("[DEBUG] Posting top-level issue comment...")
                pull_request.create_issue_comment(body=issue_comment_body)

        # Now post inline comments
        post_comments(
            comments=all_comments,
            diffs=diffs_by_file,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            commit_id=commit_id,
            github_token=github_token,
            line_maps=line_maps
        )
        print(f"[DEBUG] PR head commit SHA: {pr.head.sha}")
        print(f"[DEBUG] Using commit_id: {commit_id}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()