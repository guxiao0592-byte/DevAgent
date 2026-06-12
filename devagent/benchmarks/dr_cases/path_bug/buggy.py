"""File path handling bugs."""


def read_file_safe(filename):
    """Read a file and return its contents."""
    with open(filename, "r") as f:
        return f.read()


def count_lines(filename):
    """Count lines in a file."""
    content = read_file_safe(filename)
    lines = content.split("\n")
    return len(lines)


def write_report(filename, content):
    """Write report content to file."""
    f = open(filename, "w")
    f.write(content)
    f.close()


def list_files(directory):
    """List all .txt files in a directory."""
    import os
    files = []
    for f in os.listdir(directory):
        if f.endswith(".txt"):
            files.append(f)
    return files
