import os
import sys
import json
import requests
from github import Github
from git import Git, Repo
import re

def get_contextual_files(repo_path):
    manual_content = ''
    example_contents = ''

    # Use the GITHUB_WORKSPACE environment variable
    repo_path = os.environ.get('GITHUB_WORKSPACE', '/github/workspace')

    # Read developer manual
    manual_path = os.path.join(repo_path, 'developer_manual.md')
    if os.path.exists(manual_path):
        with open(manual_path, 'r') as f:
            manual_content = f.read()
    else:
        print("No developer_manual.md found.")

    # Read example files
    examples_path = os.path.join(repo_path, 'examples')
    if os.path.exists(examples_path):
        for root, dirs, files in os.walk(examples_path):
            for file in files:
                with open(os.path.join(root, file), 'r') as f:
                    content = f.read()
                    example_contents += f"\n### Example File: {file}\n{content}"
    else:
        print("No examples directory found.")

    return manual_content, example_contents

def get_changed_files(repo, base_branch, head_branch, file_extensions):
    origin = repo.remotes.origin

    print(f"[DEBUG] Fetching branches...")
    origin.fetch()

    # Ensure the base branch exists locally
    if base_branch not in repo.heads:
        print(f"[DEBUG] Base branch {base_branch} not found locally. Creating it.")
        repo.create_head(base_branch, origin.refs[base_branch])
    else:
        repo.heads[base_branch].set_tracking_branch(origin.refs[base_branch])

    # Checkout the head branch
    print(f"[DEBUG] Checking out head branch: {head_branch}")
    repo.git.checkout(head_branch)

    # Get the common ancestor
    base_commit = repo.merge_base(base_branch, head_branch)
    if not base_commit:
        raise Exception(f"Could not find common ancestor between {base_branch} and {head_branch}")

    print(f"[DEBUG] Found common ancestor: {base_commit[0].hexsha}")

    # Get the diff between the base commit and head
    diff_index = base_commit[0].diff(head_branch, create_patch=True)

    filtered_diffs = []
    print("[DEBUG] Filtering diffs by file extensions:", file_extensions)
    for diff in diff_index:
        # Determine file path
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

def review_code_with_llm(filename, diff_content, manual_content, example_contents, api_key):
    prompt = f"""
You are a code reviewer. Below is the developer manual and examples, if they are empty base your review on best practice for safe and efficient code:

Developer Manual:
{manual_content}

Examples:
{example_contents}

Now, review the following code diff and provide feedback:

File: {filename}
Diff:
{diff_content}

Provide your feedback in the following JSON format:
{{
  "filename": "{filename}",
  "comments": [
    {{
      "line": integer,  # Line number in the new code
      "comment": "string"
    }},
    ...
  ]
}}
"""

    print("[DEBUG] Sending request to LLM for file:", filename)
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
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

def get_position_in_diff(diff, target_line):
    print("[DEBUG] Calculating position in diff for target_line:", target_line)
    position = 0
    current_line = None

    # Print a snippet of the diff for debugging
    diff_lines = diff.split('\n')
    print("[DEBUG] Diff lines:")
    for dl in diff_lines:
        if dl.startswith('@@') or dl.startswith('+') or dl.startswith('-'):
            print("   ", dl)

    for idx, line in enumerate(diff_lines):
        if line.startswith('@@'):
            # Extract hunk info
            match = re.search(r'\+(\d+)(?:,(\d+))?', line)
            if match:
                current_line = int(match.group(1)) - 1
            print(f"[DEBUG] Hunk line {idx}: {line}, starting at line {current_line+1} in the new file")
        elif line.startswith('+'):
            current_line += 1
            position += 1
            if current_line == target_line:
                print(f"[DEBUG] Found position at {position} for line {target_line}")
                return position
        elif line.startswith('-'):
            # Removed line, does not advance position, but does it advance current_line?
            # Actually, '-' lines do not affect 'position' since they are old lines removed.
            # They do not increment position. But we still increment current_line for context lines?
            # Actually, for unchanged lines (no +/-), we increment current_line as well.
            # Here we skip incrementing position because it's not a newly added line.
            continue
        else:
            # Context line (no + or -)
            current_line += 1

    print("[DEBUG] No position found for target_line:", target_line)
    return None

def post_comments(comments, diffs, repo_full_name, pr_number, commit_id, github_token):
    from github import GithubException  # Import GithubException for better error handling
    g = Github(github_token)
    repo = g.get_repo(repo_full_name)
    pull_request = repo.get_pull(pr_number)

    print("[DEBUG] Posting comments...")
    print(f"[DEBUG] PR Number: {pr_number}, commit_id: {commit_id}, repo_full_name: {repo_full_name}")

    for comment in comments:
        filename = comment['filename']
        line = comment['line']
        body = comment['comment']

        print(f"[DEBUG] Attempting to post comment on {filename} at line {line} with body: {body}")

        file_diff = diffs.get(filename)
        if not file_diff:
            print(f"Could not find diff for {filename}, skipping comment.")
            continue

        position = get_position_in_diff(file_diff, line)
        print(f"[DEBUG] Computed position: {position} for file {filename}, line {line}")

        if position is None:
            print(f"Could not find position for file {filename} line {line}, skipping comment.")
            continue

        # Print a small snippet of the diff around the target line for debugging
        diff_lines = file_diff.split('\n')
        start_context = max(0, position - 5)
        end_context = min(len(diff_lines), position + 5)
        print("[DEBUG] Diff context around position:")
        for c_idx in range(start_context, end_context):
            print(f"   {c_idx}: {diff_lines[c_idx]}")

        try:
            print(f"[DEBUG] Creating review comment with commit_id={commit_id}, path={filename}, position={position}")
            pull_request.create_review_comment(
                body=body,
                commit_id=commit_id,
                path=filename,
                position=position
            )
            print(f"Comment posted on {filename} line {line}")
        except GithubException as e:
            print(f"Error posting comment on {filename} line {line}: {e.data}")
            # Remove sys.exit(1) here temporarily to continue processing

        except Exception as e:
            print("[DEBUG] Generic exception encountered while posting comment")
            print(f"[DEBUG] Exception type: {type(e)}")
            print("[DEBUG] Exception content:", str(e))
            # Remove sys.exit(1) here temporarily to continue processing

def main():
    try:
        openai_api_key = sys.argv[1]
        file_types_input = sys.argv[2] if len(sys.argv) > 2 else ''
        github_token = os.environ.get('GITHUB_TOKEN')

        # Parse file types
        if file_types_input:
            file_extensions = [ext.strip() for ext in file_types_input.split(',')]
        else:
            file_extensions = []
        print("[DEBUG] File extensions:", file_extensions)

        # Initialize GitHub client
        g = Github(github_token)
        repo_full_name = os.environ['GITHUB_REPOSITORY']
        print("[DEBUG] Repo Full Name:", repo_full_name)
        repo = g.get_repo(repo_full_name)

        # Get PR information
        event_path = os.environ['GITHUB_EVENT_PATH']
        print("[DEBUG] Reading event from:", event_path)
        with open(event_path, 'r') as f:
            event = json.load(f)

        pr_number = event['pull_request']['number']
        pr = repo.get_pull(pr_number)
        base_branch = pr.base.ref
        head_branch = pr.head.ref
        commit_id = pr.head.sha

        print(f"[DEBUG] PR Number: {pr_number}, Base Branch: {base_branch}, Head Branch: {head_branch}, Commit ID: {commit_id}")

        # Clone the repo
        repo_path = '/github/workspace'  # Already checked out by actions/checkout
        os.chdir(repo_path)

        # Configure Git to consider the directory safe
        git_cmd = Git(repo_path)
        git_cmd.config('--global', '--add', 'safe.directory', repo_path)

        # Initialize Repo object
        repo_git = Repo(repo_path)

        # Proceed with your Git operations
        diffs = get_changed_files(repo_git, base_branch, head_branch, file_extensions)

        # Get contextual files
        manual_content, example_contents = get_contextual_files(repo_path)

        # Prepare code snippets and review each file
        all_comments = []
        diffs_by_file = {}
        for diff in diffs:
            if diff.a_path:
                filename = diff.a_path
            else:
                filename = diff.b_path

            print(f"Reviewing {filename}...")
            diff_content = diff.diff.decode('utf-8', errors='replace')
            diffs_by_file[filename] = diff_content

            # Log a snippet of the diff for debugging
            print("[DEBUG] Diff content snippet for", filename)
            for line_idx, dline in enumerate(diff_content.split('\n')[:10]):
                print(f"   {line_idx}: {dline}")

            llm_response = review_code_with_llm(
                filename=filename,
                diff_content=diff_content,
                manual_content=manual_content,
                example_contents=example_contents,
                api_key=openai_api_key
            )

            # Log the entire LLM response
            print("[DEBUG] LLM response:", llm_response)

            # Parse LLM response
            try:
                llm_text = llm_response['choices'][0]['message']['content']
                # Sometimes the model might output text before or after the JSON. Extract JSON.
                json_start = llm_text.find('{')
                json_end = llm_text.rfind('}') + 1
                llm_json_str = llm_text[json_start:json_end]
                feedback = json.loads(llm_json_str)

                # Log the parsed feedback
                print("[DEBUG] Parsed feedback JSON:", feedback)

                comments = feedback.get('comments', [])
                for c in comments:
                    c['filename'] = filename
                all_comments.extend(comments)
            except Exception as e:
                print(f"Error parsing LLM response for {filename}: {e}")
                # Log the llm_text if parsing fails
                print("[DEBUG] LLM text that failed to parse:", llm_text if 'llm_text' in locals() else "No llm_text variable")
                continue

        # Post comments
        post_comments(
            comments=all_comments,
            diffs=diffs_by_file,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            commit_id=commit_id,
            github_token=github_token
        )

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
