# Makefile for scheduling_CS project
# Developed by Fabian Torres & Pierre Hellich
# Semester project Fall 2023

# Compiler settings
CC = gcc
CFLAGS = -Wall -Wextra -O2 -std=c11 -Iinclude
LDFLAGS = -lm

# Directories
SRC_DIR = src
INC_DIR = include
OBJ_DIR = obj
BIN_DIR = bin

# Target executable name
TARGET = $(BIN_DIR)/scheduling

# Source files (with paths)
SOURCES = $(SRC_DIR)/scheduling.c $(SRC_DIR)/main.c $(SRC_DIR)/utils.c
HEADERS = $(INC_DIR)/scheduling.h $(INC_DIR)/utils.h

# Object files (in obj directory)
OBJECTS = $(OBJ_DIR)/scheduling.o $(OBJ_DIR)/main.o $(OBJ_DIR)/utils.o

# Default target - builds the executable
all: $(TARGET)

# Link object files to create the executable
$(TARGET): $(OBJECTS) | $(BIN_DIR)
	@echo "Linking $@..."
	$(CC) $(OBJECTS) -o $(TARGET) $(LDFLAGS)
	@echo "Build successful! Executable: $(TARGET)"

# Create directories if they don't exist
$(BIN_DIR):
	@mkdir -p $(BIN_DIR)

$(OBJ_DIR):
	@mkdir -p $(OBJ_DIR)

# Compile .c files to .o files in obj directory
$(OBJ_DIR)/%.o: $(SRC_DIR)/%.c $(HEADERS) | $(OBJ_DIR)
	@echo "Compiling $<..."
	$(CC) $(CFLAGS) -c $< -o $@

# Clean up build artifacts
clean:
	@echo "Cleaning up..."
	rm -rf $(OBJ_DIR) $(BIN_DIR)
	rm -rf *.dSYM
	@echo "Clean complete!"

# Clean and rebuild everything
rebuild: clean all

# Run the program (requires implementation of proper initialization)
run: $(TARGET)
	$(TARGET)

# Debug build (with debugging symbols and no optimization)
debug: CFLAGS = -Wall -Wextra -g -std=c11
debug: clean $(TARGET)
	@echo "Debug build complete!"

# Show help
help:
	@echo "Available targets:"
	@echo "  make          - Build the project (default)"
	@echo "  make all      - Same as 'make'"
	@echo "  make clean    - Remove all build artifacts"
	@echo "  make rebuild  - Clean and rebuild everything"
	@echo "  make run      - Build and run the program"
	@echo "  make debug    - Build with debug symbols"
	@echo "  make test     - Build and run test suite"
	@echo "  make test-build - Build tests only (don't run)"
	@echo "  make test-clean - Clean test artifacts"
	@echo "  make help     - Show this help message"

# Test targets (delegates to tests/Makefile)
test:
	@echo "Running tests..."
	$(MAKE) -C tests test

test-build:
	@echo "Building tests..."
	$(MAKE) -C tests

test-clean:
	@echo "Cleaning tests..."
	$(MAKE) -C tests clean

# Python helpers (default to conda env dp_new)
PY_ENV ?= dp_new
PY := conda run -n $(PY_ENV) python

py-testing-check:
	$(PY) testing_latest/testing_check.py

# Phony targets (not actual files)
.PHONY: all clean rebuild run debug help test test-build test-clean py-testing-check
