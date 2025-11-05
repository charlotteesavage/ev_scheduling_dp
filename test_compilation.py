#!/usr/bin/env python3
"""
Test script to verify the C code compiles correctly and works with Python ctypes.
"""

import subprocess
import os
from ctypes import CDLL, c_int, c_double, POINTER

def test_compilation():
    """Test if the C code compiles into a shared library."""
    print("=" * 60)
    print("Testing C code compilation...")
    print("=" * 60)

    current_dir = os.path.dirname(os.path.abspath(__file__))

    # Compile command
    compile_command = [
        "gcc",
        "-shared",
        "-fPIC",
        "-O2",
        "-o", f"{current_dir}/scheduling_CS.so",
        f"{current_dir}/scheduling_CS.c",
        f"{current_dir}/scheduling_main.c",
        "-lm",
    ]

    print(f"Compilation command:")
    print(f"  {' '.join(compile_command)}")
    print()

    result = subprocess.run(compile_command, capture_output=True, text=True)

    if result.returncode != 0:
        print("❌ COMPILATION FAILED!")
        print(f"STDERR:\n{result.stderr}")
        return False
    else:
        print("✅ Compilation successful!")
        print(f"   Created: scheduling_CS.so")
        return True

def test_library_loading():
    """Test if the shared library can be loaded."""
    print("\n" + "=" * 60)
    print("Testing library loading...")
    print("=" * 60)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    so_path = f"{current_dir}/scheduling_CS.so"

    if not os.path.exists(so_path):
        print(f"❌ Shared library not found at: {so_path}")
        return False

    try:
        lib = CDLL(so_path)
        print(f"✅ Library loaded successfully from: {so_path}")
        return lib
    except Exception as e:
        print(f"❌ Failed to load library: {e}")
        return None

def test_function_access(lib):
    """Test if we can access the C functions."""
    print("\n" + "=" * 60)
    print("Testing function access...")
    print("=" * 60)

    functions_to_test = [
        "set_general_parameters",
        "set_activities",
        "initialize_charge_rates",
        "create_bucket",
        "free_bucket",
        "DP",
        "get_count",
        "get_total_time",
        "get_final_schedule",
    ]

    all_found = True
    for func_name in functions_to_test:
        try:
            func = getattr(lib, func_name)
            print(f"  ✅ Found: {func_name}")
        except AttributeError:
            print(f"  ❌ Missing: {func_name}")
            all_found = False

    return all_found

if __name__ == "__main__":
    print("\n🔧 Scheduling CS - Compilation & Interface Test\n")

    # Test 1: Compilation
    if not test_compilation():
        print("\n❌ Tests failed at compilation stage")
        exit(1)

    # Test 2: Library loading
    lib = test_library_loading()
    if lib is None:
        print("\n❌ Tests failed at library loading stage")
        exit(1)

    # Test 3: Function access
    if not test_function_access(lib):
        print("\n⚠️  Some functions are missing, but library loaded")
        exit(1)

    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)
    print("\nYour C code is properly structured and ready to use with Python!")
    print("You can now use main_slice_cs.py to run the optimizer.\n")
