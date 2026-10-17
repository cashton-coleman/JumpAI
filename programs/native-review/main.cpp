// Native source-review operations: inventory, textual audit, outcome summary, report.
// JSON for Modern C++ v3.12.0 is bundled under LICENSE.MIT; no runtime downloads.
#include "json.hpp"
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>
#include <sstream>

using Json = nlohmann::json;
namespace fs = std::filesystem;

// Read a complete file as bytes; missing files and read errors terminate the operation.
std::string read_file(const fs::path& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("Cannot open: " + path.string());
    std::ostringstream data;
    data << stream.rdbuf();
    if (stream.bad()) throw std::runtime_error("Cannot read: " + path.string());
    return data.str();
}

// Resolve a requested relative file beneath root, rejecting symlink/path escapes.
fs::path source_path(const Json& args, const std::string& name) {
    auto root = fs::canonical(args.at("root").get<std::string>());
    auto relative = fs::path(name);
    if (relative.is_absolute()) throw std::runtime_error("Expected relative source path");
    auto path = fs::canonical(root / relative);
    auto within = path.lexically_relative(root);
    if (within.empty() || *within.begin() == "..") throw std::runtime_error("Source escapes root");
    return path;
}

// Count bytes and LF-delimited lines per file, preserving deterministic filename order.
Json inventory(const Json& args) {
    auto names = args.at("files").get<std::vector<std::string>>();
    std::sort(names.begin(), names.end());
    Json rows = Json::array();
    uint64_t bytes = 0, lines = 0;
    for (const auto& name : names) {
        auto data = read_file(source_path(args, name));
        uint64_t count = std::count(data.begin(), data.end(), '\n');
        if (!data.empty() && data.back() != '\n') ++count;
        rows.push_back({{"file", name}, {"bytes", data.size()}, {"lines", count}});
        bytes += data.size();
        lines += count;
    }
    return {{"files_scanned", rows.size()}, {"bytes", bytes}, {"lines", lines}, {"files", rows}};
}

// Scan literal TODO/FIXME markers and trailing spaces/tabs; this is not a syntax linter.
Json audit(const Json& args) {
    auto names = args.at("files").get<std::vector<std::string>>();
    std::sort(names.begin(), names.end());
    Json markers = Json::array(), trailing = Json::array();
    for (const auto& name : names) {
        std::istringstream stream(read_file(source_path(args, name)));
        std::string line;
        size_t number = 0;
        while (std::getline(stream, line)) {
            ++number;
            if (!line.empty() && line.back() == '\r') line.pop_back();
            for (const auto& marker : {"TODO", "FIXME"}) {
                if (line.find(marker) != std::string::npos)
                    markers.push_back({{"file", name}, {"line", number}, {"marker", marker}});
            }
            if (!line.empty() && (line.back() == ' ' || line.back() == '\t'))
                trailing.push_back({{"file", name}, {"line", number}});
        }
    }
    return {{"files_scanned", names.size()}, {"markers", markers}, {"trailing_whitespace", trailing}};
}

// Validate recorded outcomes and summarize counts, failures, and the three slowest tests.
Json summarize(const Json& args) {
    auto records = Json::parse(read_file(args.at("path").get<std::string>()));
    if (!records.is_array()) throw std::runtime_error("Expected outcome array");
    const std::set<std::string> allowed = {"passed", "failed", "error", "skipped",
                                         "expected_failure", "unexpected_success"};
    Json counts = Json::object(), failures = Json::array(), slowest = Json::array();
    double total = 0;
    for (const auto& row : records) {
        auto name = row.at("test").get<std::string>();
        auto status = row.at("status").get<std::string>();
        if (!allowed.count(status) || !row.at("duration_ms").is_number())
            throw std::runtime_error("Invalid outcome record");
        double duration = row.at("duration_ms").get<double>();
        if (!std::isfinite(duration) || duration < 0) throw std::runtime_error("Invalid duration");
        counts[status] = counts.value(status, 0) + 1;
        total += duration;
        if (status == "failed" || status == "error" || status == "unexpected_success")
            failures.push_back(name);
    }
    if (!std::isfinite(total)) throw std::runtime_error("Duration total overflow");
    std::sort(failures.begin(), failures.end());
    std::stable_sort(records.begin(), records.end(), [](const Json& left, const Json& right) {
        // Keep ties deterministic by test ID, preserving input order for identical keys.
        if (left.at("duration_ms") != right.at("duration_ms"))
            return left.at("duration_ms") > right.at("duration_ms");
        return left.at("test") < right.at("test");
    });
    for (size_t index = 0; index < std::min<size_t>(3, records.size()); ++index)
        slowest.push_back({{"test", records[index].at("test")}, {"duration_ms", records[index].at("duration_ms")}});
    return {{"tests", records.size()}, {"statuses", counts}, {"total_test_ms", std::round(total * 1000) / 1000},
            {"failures", failures}, {"slowest", slowest}};
}

// Dispatch the manifest's fixed operation; emit one JSON value or a nonzero diagnostic.
int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::runtime_error("Expected one operation");
        const auto args = Json::parse(std::cin);
        const std::string operation = argv[1];
        Json result;
        if (operation == "inventory") result = inventory(args);
        else if (operation == "audit") result = audit(args);
        else if (operation == "summarize") result = summarize(args);
        else if (operation == "combine") result = args;
        else throw std::runtime_error("Unknown operation");
        std::cout << result.dump() << '\n';
        return std::cout ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
