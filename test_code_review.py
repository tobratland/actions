import unittest
from unittest.mock import Mock, patch, MagicMock, PropertyMock, create_autospec
import git
from git import GitCommandError, Git
import io
from pathlib import Path

from code_review import get_called_functions, get_changed_files, review_code_with_llm

class TestCodeReviewAction(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures with proper mock configuration"""
        # Create mock repo and diff objects
        self.mock_repo = Mock(spec=git.Repo)
        self.mock_origin = Mock(spec=git.Remote)
        self.mock_diff = Mock(spec=git.Diff)
        self.mock_base_commit = Mock(spec=git.Commit)
        
        # Create a proper Git command mock with all required methods
        git_mock = create_autospec(Git)
        git_mock.checkout = Mock(return_value=None)
        git_mock.diff = Mock(return_value="")
        git_mock.config = Mock(return_value=None)
        self.mock_repo.git = git_mock
        
        # Set up mock repository structure
        self.mock_repo.remotes.origin = self.mock_origin
        
        # Properly mock the refs dictionary-like behavior
        mock_refs = MagicMock()
        self.mock_origin.refs = mock_refs
        mock_ref = Mock(spec=git.Reference)
        mock_ref.commit = self.mock_base_commit
        self.mock_origin.refs.__getitem__.side_effect = lambda x: mock_ref
        
        # Mock the heads property
        mock_heads = MagicMock()
        type(self.mock_repo).heads = PropertyMock(return_value=mock_heads)
        mock_heads.__contains__.return_value = False  # Simulate branch not existing
        
        # Set up mock head object
        mock_head = Mock(spec=git.Head)
        mock_head.commit = self.mock_base_commit
        self.mock_repo.create_head.return_value = mock_head
        
        # Set up merge base
        self.mock_repo.merge_base.return_value = [self.mock_base_commit]

    def create_mock_diff(self, file_path, content, encoding='utf-8'):
        """Helper to create a mock diff with specific content"""
        mock_diff = Mock(spec=git.Diff)
        mock_diff.a_path = file_path
        mock_diff.b_path = file_path
        
        # Handle different content types
        if isinstance(content, str):
            mock_diff.diff = content.encode(encoding)
        elif isinstance(content, bytes):
            mock_diff.diff = content
        else:
            mock_diff.diff = str(content).encode(encoding)
            
        return mock_diff
    
    def test_get_changed_files_production_scenario(self):
        """Test the actual production scenario where the error occurs"""
        # Arrange
        file_extensions = ['.rs', '.md', '.toml']
        
        # Create mock diffs as they appear in production
        mock_diff_1 = Mock(spec=git.Diff)
        mock_diff_1.a_path = "2024/day_04/Cargo.toml"
        mock_diff_1.b_path = "2024/day_04/Cargo.toml"
        # Don't set diff attribute yet - simulating production behavior
        
        mock_diff_2 = Mock(spec=git.Diff)
        mock_diff_2.a_path = "2024/day_04/src/lib.rs"
        mock_diff_2.b_path = "2024/day_04/src/lib.rs"
        # Don't set diff attribute yet - simulating production behavior
        
        self.mock_base_commit.diff.return_value = [mock_diff_1, mock_diff_2]
        
        # Act & Assert
        try:
            filtered_diffs, diffs_by_file = get_changed_files(
                self.mock_repo, "main", "feature", file_extensions
            )
            print("[DEBUG] Successfully processed diffs")
            self.assertEqual(len(filtered_diffs), 2)
            self.assertIn("2024/day_04/Cargo.toml", diffs_by_file)
            self.assertIn("2024/day_04/src/lib.rs", diffs_by_file)
        except Exception as e:
            self.fail(f"get_changed_files raised an exception: {str(e)}")

    def test_get_called_functions_production_scenario(self):
        """Test function call detection with production-like input"""
        test_cases = [
            (None, set()),  # Handle None input
            (b"", set()),   # Handle empty bytes
            ("", set()),    # Handle empty string
            (
                """@@ -1,3 +1,4 @@
                +fn new_function() {
                -fn old_function() {
                    println!("Hello");
                }""",
                {"new_function"}
            ),
            (
                b"""@@ -1,3 +1,4 @@
                +fn new_function() {
                -fn old_function() {
                    println!("Hello");
                }""",
                {"new_function"}
            ),
            # Test indentation cases
            (
                b"+    fn indented_function() {",
                {"indented_function"}
            ),
            (
                b"+fn no_indent_function() {",
                {"no_indent_function"}
            ),
        ]
        
        for input_content, expected_functions in test_cases:
            with self.subTest(input_content=input_content):
                result = get_called_functions(input_content)
                self.assertEqual(result, expected_functions, 
                            f"Failed for input: {input_content}")

    def test_get_changed_files_with_missing_diff(self):
        """Test handling of diffs without diff attribute or content"""
        # Arrange
        file_extensions = ['.rs']
        mock_diff = Mock(spec=git.Diff)
        mock_diff.a_path = "test.rs"
        mock_diff.b_path = "test.rs"
        
        # Make accessing diff attribute raise AttributeError
        def raise_attribute_error(_):
            raise AttributeError("'Diff' object has no attribute 'diff'")
        
        mock_diff.__getattr__ = raise_attribute_error
        
        self.mock_base_commit.diff.return_value = [mock_diff]
        
        # Make git.diff() fail to force the error path
        def raise_type_error(*args, **kwargs):
            raise TypeError("expected string or bytes-like object")
        
        self.mock_repo.git.diff = raise_type_error
        
        # Act & Assert
        with self.assertRaises(TypeError) as context:
            filtered_diffs, diffs_by_file = get_changed_files(
                self.mock_repo, "main", "feature", file_extensions
            )
        
        self.assertEqual(str(context.exception), 
                        "expected string or bytes-like object")

    def test_get_changed_files_with_missing_diff_and_raw_bytes(self):
        """Test handling of diffs when primary method fails but raw bytes exist"""
        # Arrange
        file_extensions = ['.rs']
        mock_diff = Mock(spec=git.Diff)
        mock_diff.a_path = "test.rs"
        mock_diff.b_path = "test.rs"
        
        # Simulate malformed bytes content
        class MalformedBytes:
            def decode(self, *args, **kwargs):
                raise TypeError("expected string or bytes-like object")
        
        mock_diff.diff = MalformedBytes()
        
        self.mock_base_commit.diff.return_value = [mock_diff]
        
        # Make git.diff() fail to ensure we're using the primary diff content
        self.mock_repo.git.diff.side_effect = TypeError("should not be called")
        
        # Act & Assert
        with self.assertRaises(TypeError) as context:
            filtered_diffs, diffs_by_file = get_changed_files(
                self.mock_repo, "main", "feature", file_extensions
            )
        
        self.assertEqual(str(context.exception), 
                        "expected string or bytes-like object")

    def test_get_changed_files_with_string_diff(self):
        """Test handling of string diff content"""
        # Arrange
        file_extensions = ['.rs']
        test_diff_content = """
@@ -1,3 +1,4 @@
+fn new_function() {
-fn old_function() {
     println!("Hello");
 }
"""
        mock_diff = self.create_mock_diff("test.rs", test_diff_content)
        self.mock_base_commit.diff.return_value = [mock_diff]
        self.mock_repo.git.diff.return_value = test_diff_content
        
        # Act
        filtered_diffs, diffs_by_file = get_changed_files(
            self.mock_repo, "main", "feature", file_extensions
        )
        
        # Assert
        self.assertEqual(len(filtered_diffs), 1)
        self.assertIn("test.rs", diffs_by_file)
        self.assertIsInstance(diffs_by_file["test.rs"], str)

    def test_get_changed_files_with_bytes_diff(self):
        """Test handling of bytes diff content"""
        # Arrange
        file_extensions = ['.rs']
        test_diff_content = b"""
@@ -1,3 +1,4 @@
+fn new_function() {
-fn old_function() {
     println!("Hello");
 }
"""
        mock_diff = self.create_mock_diff("test.rs", test_diff_content)
        self.mock_base_commit.diff.return_value = [mock_diff]
        self.mock_repo.git.diff.return_value = test_diff_content.decode('utf-8')
        
        # Act
        filtered_diffs, diffs_by_file = get_changed_files(
            self.mock_repo, "main", "feature", file_extensions
        )
        
        # Assert
        self.assertEqual(len(filtered_diffs), 1)
        self.assertIn("test.rs", diffs_by_file)
        self.assertIsInstance(diffs_by_file["test.rs"], str)

    def test_get_called_functions_with_various_inputs(self):
        """Test function call detection with different input types"""
        test_cases = [
            # String input with added lines only
            (
                "+    call_function();\n",
                {"call_function"}
            ),
            # Bytes input with added lines only
            (
                b"+    bytes_function();\n",
                {"bytes_function"}
            ),
            # Mixed content with only added lines containing function calls
            (
                "+    first_call()\n-    old_call()\n+    second_call()",
                {"first_call", "second_call"}
            ),
            # No function calls
            (
                "+    let x = 5;\n-    let y = 10;",
                set()
            ),
            # Multiple calls on one line
            (
                "+    first_call(second_call())",
                {"first_call", "second_call"}
            )
        ]
        
        for input_content, expected_functions in test_cases:
            with self.subTest(input_content=input_content):
                result = get_called_functions(input_content)
                self.assertEqual(result, expected_functions, 
                               f"Failed for input: {input_content}")

    def test_error_handling(self):
        """Test error handling for invalid inputs"""
        # Test with None values
        self.assertEqual(get_called_functions(None), set())
        
        # Test with invalid Unicode
        invalid_bytes = b"\xff\xfe\x00\x00" # Invalid UTF-8
        result = get_called_functions(invalid_bytes)
        self.assertEqual(result, set())
        
        # Test with empty content
        self.assertEqual(get_called_functions(""), set())
        self.assertEqual(get_called_functions(b""), set())
        
        # Test git error handling
        self.mock_repo.git.diff.side_effect = GitCommandError('diff', status=128)
        self.mock_repo.git.checkout.side_effect = GitCommandError('checkout', status=128)
        
        with self.assertRaises(Exception):
            filtered_diffs, diffs_by_file = get_changed_files(
                self.mock_repo, "main", "feature", ['.rs']
            )

    @patch('requests.post')
    def test_review_code_with_llm_input_handling(self, mock_post):
        """Test LLM review with different input types"""
        mock_response = {
            "choices": [{
                "message": {
                    "content": """
{
    "filename": "test.rs",
    "chunk": 1,
    "comments": [
        {
            "line": 1,
            "comment": "Test comment"
        }
    ]
}
"""
                }
            }]
        }
        
        mock_post.return_value.json.return_value = mock_response
        
        test_cases = [
            # String diff
            "+fn test() {}\n",
            # Bytes diff
            b"+fn test() {}\n",
            # Mixed content
            "+fn new_function() {}\n-fn old_function() {}\n"
        ]
        
        for diff_content in test_cases:
            with self.subTest(diff_content=diff_content):
                result = review_code_with_llm(
                    filename="test.rs",
                    diff_content=diff_content,
                    manual_content="",
                    example_contents="",
                    issue_content="",
                    function_definitions="",
                    api_key="test_key"
                )
                
                self.assertIsInstance(result, list)
                self.assertEqual(len(result), 1)

if __name__ == '__main__':
    unittest.main()