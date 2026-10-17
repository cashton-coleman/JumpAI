// Forward the assembled repository report through Jump AI's validated JSON contract.
#include <iostream>

// Copy the complete report without reparsing it; fail on input or output errors.
// The runner validates JSON before invocation and validates the returned object.
int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    std::cout << std::cin.rdbuf();
    std::cout.flush();
    return std::cin.bad() || !std::cout ? 1 : 0;
}
