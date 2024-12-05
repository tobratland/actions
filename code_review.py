import os
import sys
import json
import requests
from github import Github
from git import Git, Repo
import re

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

def get_file_with_changes(repo, filename, base_branch, head_branch):
    """Get the complete file content along with information about changed lines."""
    try:
        # Get the current (new) version of the file
        current_content = repo.get_contents(filename, ref=head_branch).decoded_content.decode('utf-8')
        
        # Get the diff to identify changed lines
        base_commit = repo.merge_base(base_branch, head_branch)[0]
        diff = base_commit.diff(head_branch, paths=[filename])[0]
        
        # Parse the diff to get changed line numbers
        changed_lines = set()
        current_line = 0
        for line in diff.diff.decode('utf-8').split('\n'):
            if line.startswith('@@'):
                match = re.search(r'\+(\d+)', line)
                if match:
                    current_line = int(match.group(1)) - 1
            elif line.startswith('+'):
                current_line += 1
                changed_lines.add(current_line)
            elif not line.startswith('-'):
                current_line += 1
        
        return {
            'content': current_content,
            'changed_lines': sorted(list(changed_lines)),
            'diff': diff.diff.decode('utf-8')
        }
    except Exception as e:
        print(f"[DEBUG] Error getting file content: {e}")
        return None

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
        raise Exception(f"Could not find common ancestor between {base_branch} and {head_branch}")

    diff_index = base_commit[0].diff(head_branch, create_patch=True)

    print("[DEBUG] Filtering diffs by file extensions:", file_extensions)
    filtered_files = []
    for diff in diff_index:
        file_path = diff.a_path or diff.b_path
        if any(file_path.endswith(ext) for ext in file_extensions):
            print(f"[DEBUG] Including file: {file_path}")
            file_info = get_file_with_changes(repo, file_path, base_branch, head_branch)
            if file_info:
                filtered_files.append((file_path, file_info))
        else:
            print(f"[DEBUG] Skipping file not matching extensions: {file_path}")

    return filtered_files

def review_code_with_llm(filename, file_info, manual_content, example_contents, api_key):
    prompt = f"""
You are a code reviewer. Below is the developer manual and examples. If they are empty, base your review on best practices for safe and efficient code:

Developer Manual:
{manual_content}

Examples:
{example_contents}

Now, review the following file. Focus on the changed lines (provided in the changed_lines list) while considering the complete file context:

File: {filename}
Complete File Content:
{file_info['content']}

Changed Lines: {file_info['changed_lines']}

Diff for reference:
{file_info['diff']}

Provide your feedback in the following JSON format:
{{
  "filename": "{filename}",
  "comments": [
    {{
      "line": integer,  # Line number in the new code
      "comment": "string"  # Your review comment focusing on the changes in context
    }},
    ...
  ]
}}
"""

    print("[DEBUG] Sending request to LLM for file:", filename)
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
        json=payload
    )

    print("[DEBUG] LLM Response status code:", response.status_code)
    return response.json()

def main():
    try:
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

        print(f"[DEBUG] PR Number: {pr_number}, Base Branch: {base_branch}, Head Branch: {head_branch}, Commit ID: {commit_id}")

        repo_path = "/github/workspace"
        os.chdir(repo_path)

        git_cmd = Git(repo_path)
        git_cmd.config("--global", "--add", "safe.directory", repo_path)

        repo_git = Repo(repo_path)
        changed_files = get_changed_files(repo_git, base_branch, head_branch, file_extensions)
        manual_content, example_contents = get_contextual_files(repo_path)

        all_comments = []
        diffs_by_file = {}
        
        for filename, file_info in changed_files:
            print(f"Reviewing {filename}...")
            diffs_by_file[filename] = file_info['diff']

            llm_response = review_code_with_llm(
                filename=filename,
                file_info=file_info,
                manual_content=manual_content,
                example_contents=example_contents,
                api_key=openai_api_key,
            )

            try:
                llm_text = llm_response["choices"][0]["message"]["content"]
                json_start = llm_text.find("{")
                json_end = llm_text.rfind("}") + 1
                llm_json_str = llm_text[json_start:json_end]
                feedback = json.loads(llm_json_str)

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

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()