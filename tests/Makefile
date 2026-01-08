# Makefile for DP Scheduling Algorithm Tests

CC = gcc
CFLAGS = -I../include -Wall -Wextra -g -DTESTING
LDFLAGS = -lm

SRC_DIR = ../src
INC_DIR = ../include
TEST_DIR = .

# Source files
SOURCES = $(SRC_DIR)/scheduling.c $(SRC_DIR)/utils.c
TEST_SOURCES = test_scheduling.c

# Object files
OBJECTS = $(SOURCES:.c=.o)
TEST_OBJECTS = $(TEST_SOURCES:.c=.o)

# Executables
TEST_EXEC = test_scheduling

# Default target
all: $(TEST_EXEC)

# Compile test executable
$(TEST_EXEC): $(SOURCES) $(TEST_SOURCES)
	$(CC) $(CFLAGS) -DTESTING -o $@ $(SOURCES) $(TEST_SOURCES) $(LDFLAGS)

# Clean build artifacts
clean:
	rm -f $(TEST_EXEC) *.o $(SRC_DIR)/*.o

# Run tests
test: $(TEST_EXEC)
	./$(TEST_EXEC)

# Valgrind memory check
memcheck: $(TEST_EXEC)
	valgrind --leak-check=full --show-leak-kinds=all ./$(TEST_EXEC)

.PHONY: all clean test memcheck
