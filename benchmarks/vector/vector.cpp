#include <cstdint>
#include <iostream>
#include <vector>

int main() {
    std::vector<std::int32_t> values;
    for (std::size_t index = 0; index < 20000000; ++index) {
        values.push_back(static_cast<std::int32_t>(index));
    }
    std::cout << "values.len() = " << values.size() << '\n';
}
