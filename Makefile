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

# Target executable name
TARGET = scheduling

# Source files (with paths)
SOURCES = $(SRC_DIR)/scheduling.c $(SRC_DIR)/main.c $(SRC_DIR)/utils.c
HEADERS = $(INC_DIR)/scheduling.h $(INC_DIR)/utils.h

# Object files (automatically derived from sources)
OBJECTS = $(SOURCES:.c=.o)

# Default target - builds the executable
all: $(TARGET)

# Link object files to create the executable
$(TARGET): $(OBJECTS)
	@echo "Linking $@..."
	$(CC) $(OBJECTS) -o $(TARGET) $(LDFLAGS)
	@echo "Build successful! Executable: $(TARGET)"

# Compile .c files to .o files
%.o: %.c $(HEADERS)
	@echo "Compiling $<..."
	$(CC) $(CFLAGS) -c $< -o $@

# Clean up build artifacts
clean:
	@echo "Cleaning up..."
	rm -f $(OBJECTS) $(TARGET)
	rm -rf *.dSYM
	@echo "Clean complete!"

# Clean and rebuild everything
rebuild: clean all

# Run the program (requires implementation of proper initialization)
run: $(TARGET)
	./$(TARGET)

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
	@echo "  make help     - Show this help message"

# Phony targets (not actual files)
.PHONY: all clean rebuild run debug help
