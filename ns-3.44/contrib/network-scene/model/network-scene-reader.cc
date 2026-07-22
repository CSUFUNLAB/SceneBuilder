#include "network-scene-reader.h"

#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>

namespace ns3
{

namespace
{

std::string
JoinPath(const std::string& base, const std::string& name)
{
    if (base.empty())
    {
        return name;
    }
    if (base.back() == '/')
    {
        return base + name;
    }
    return base + "/" + name;
}

bool
HasPathSeparator(const std::string& value)
{
    return value.find('/') != std::string::npos;
}

std::string
DefaultSceneRoot()
{
    const char* env = std::getenv("NS3_SCENE_ROOT");
    if (env != nullptr && std::string(env).empty() == false)
    {
        return env;
    }
#ifdef PROJECT_SOURCE_PATH
    return (std::filesystem::path(PROJECT_SOURCE_PATH).parent_path() / "generated_scenes").string();
#else
    return (std::filesystem::current_path() / "generated_scenes").string();
#endif
}

std::string
Trim(const std::string& value)
{
    auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos)
    {
        return "";
    }
    auto last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

std::vector<std::string>
SplitCsvLine(const std::string& line)
{
    std::vector<std::string> fields;
    std::string current;
    bool quoted = false;
    for (std::size_t i = 0; i < line.size(); ++i)
    {
        char c = line[i];
        if (quoted)
        {
            if (c == '"' && i + 1 < line.size() && line[i + 1] == '"')
            {
                current.push_back('"');
                ++i;
            }
            else if (c == '"')
            {
                quoted = false;
            }
            else
            {
                current.push_back(c);
            }
        }
        else if (c == '"')
        {
            quoted = true;
        }
        else if (c == ',')
        {
            fields.push_back(Trim(current));
            current.clear();
        }
        else
        {
            current.push_back(c);
        }
    }
    fields.push_back(Trim(current));
    return fields;
}

std::vector<std::map<std::string, std::string>>
ReadCsv(const std::string& path)
{
    std::ifstream input(path);
    if (!input)
    {
        throw std::runtime_error("Cannot open " + path);
    }

    std::string line;
    if (!std::getline(input, line))
    {
        return {};
    }
    auto header = SplitCsvLine(line);
    std::vector<std::map<std::string, std::string>> rows;
    while (std::getline(input, line))
    {
        if (Trim(line).empty())
        {
            continue;
        }
        auto values = SplitCsvLine(line);
        std::map<std::string, std::string> row;
        for (std::size_t i = 0; i < header.size() && i < values.size(); ++i)
        {
            row[header[i]] = values[i];
        }
        rows.push_back(row);
    }
    return rows;
}

std::string
Required(const std::map<std::string, std::string>& row, const std::string& key)
{
    auto it = row.find(key);
    if (it == row.end())
    {
        throw std::runtime_error("Missing CSV column: " + key);
    }
    return it->second;
}

std::string
Optional(const std::map<std::string, std::string>& row, const std::string& key, const std::string& fallback = "")
{
    auto it = row.find(key);
    return it == row.end() ? fallback : it->second;
}

std::string
RequiredAny(const std::map<std::string, std::string>& row, const std::string& first, const std::string& second)
{
    auto value = Optional(row, first);
    if (!value.empty())
    {
        return value;
    }
    return Required(row, second);
}

std::string
RequiredChannelState(const std::map<std::string, std::string>& row)
{
    const std::string state = Required(row, "state");
    if (state != "normal" && state != "disabled" && state != "degraded")
    {
        throw std::runtime_error("Unsupported channel state: " + state +
                                 ". Expected normal, disabled, or degraded");
    }
    return state;
}

std::string
RequiredNodeState(const std::map<std::string, std::string>& row)
{
    const std::string state = Required(row, "state");
    if (state != "normal" && state != "disabled" && state != "routing_failed")
    {
        throw std::runtime_error("Unsupported node state: " + state +
                                 ". Expected normal, disabled, or routing_failed");
    }
    return state;
}

std::string
RequiredNicState(const std::map<std::string, std::string>& row)
{
    const std::string state = Required(row, "state");
    if (state != "normal" && state != "disabled")
    {
        throw std::runtime_error("Unsupported NIC state: " + state +
                                 ". Expected normal or disabled");
    }
    return state;
}

double
RequiredChannelCapacityMultiplier(const std::map<std::string, std::string>& row,
                                  const std::string& state)
{
    const double multiplier = std::stod(Optional(row, "capacity_multiplier", "1.0"));
    if (!std::isfinite(multiplier))
    {
        throw std::runtime_error("Channel capacity_multiplier must be finite");
    }

    const bool supportedDegradation = std::abs(multiplier - 0.5) < 1e-9 ||
                                      std::abs(multiplier - 0.2) < 1e-9 ||
                                      std::abs(multiplier - 0.1) < 1e-9;
    if (state == "degraded" && !supportedDegradation)
    {
        throw std::runtime_error(
            "Degraded channel capacity_multiplier must be 0.5, 0.2, or 0.1");
    }
    if (state != "degraded" && std::abs(multiplier - 1.0) >= 1e-9)
    {
        throw std::runtime_error(
            "Normal or disabled channel capacity_multiplier must be 1.0");
    }
    return multiplier;
}

std::string
NormalizeChannelId(std::string value)
{
    if (!value.empty() && value[0] == 'L')
    {
        value[0] = 'C';
    }
    return value;
}

std::string
JsonStringValue(const std::string& line, const std::string& key)
{
    const std::string marker = "\"" + key + "\"";
    auto pos = line.find(marker);
    if (pos == std::string::npos)
    {
        return "";
    }
    pos = line.find(':', pos);
    if (pos == std::string::npos)
    {
        return "";
    }
    pos = line.find('"', pos);
    if (pos == std::string::npos)
    {
        return "";
    }
    ++pos;
    std::string value;
    bool escaped = false;
    for (; pos < line.size(); ++pos)
    {
        char c = line[pos];
        if (escaped)
        {
            value.push_back(c);
            escaped = false;
        }
        else if (c == '\\')
        {
            escaped = true;
        }
        else if (c == '"')
        {
            break;
        }
        else
        {
            value.push_back(c);
        }
    }
    return value;
}

double
JsonNumberValue(const std::string& line, const std::string& key, double fallback = 0.0)
{
    const std::string marker = "\"" + key + "\"";
    auto pos = line.find(marker);
    if (pos == std::string::npos)
    {
        return fallback;
    }
    pos = line.find(':', pos);
    if (pos == std::string::npos)
    {
        return fallback;
    }
    ++pos;
    while (pos < line.size() && std::string(" \t\r\n").find(line[pos]) != std::string::npos)
    {
        ++pos;
    }
    auto end = pos;
    while (end < line.size() && std::string("-+.0123456789eE").find(line[end]) != std::string::npos)
    {
        ++end;
    }
    if (end == pos)
    {
        return fallback;
    }
    return std::stod(line.substr(pos, end - pos));
}

} // namespace

std::string
ResolveNetworkSceneDirectory(const std::string& value)
{
    if (value.empty())
    {
        return DefaultSceneRoot();
    }

    std::filesystem::path path(value);
    if (path.is_absolute())
    {
        return value;
    }

    if (HasPathSeparator(value) && std::filesystem::exists(path))
    {
        return value;
    }

    return JoinPath(DefaultSceneRoot(), value);
}

std::string
NetworkSceneBaseName(const std::string& path)
{
    auto trimmed = path;
    while (!trimmed.empty() && trimmed.back() == '/')
    {
        trimmed.pop_back();
    }
    auto pos = trimmed.find_last_of('/');
    if (pos == std::string::npos)
    {
        return trimmed;
    }
    return trimmed.substr(pos + 1);
}

NetworkSceneData
ReadNetworkSceneData(const std::string& sceneDirectory, const std::string& eventFile)
{
    NetworkSceneData data;

    for (const auto& row : ReadCsv(JoinPath(sceneDirectory, "nodes.csv")))
    {
        data.nodes.push_back({Required(row, "node_id"), RequiredNodeState(row)});
    }

    const auto channelsPath = JoinPath(sceneDirectory, "channels.csv");
    const auto legacyLinksPath = JoinPath(sceneDirectory, "links.csv");
    const auto channelRows =
        std::filesystem::exists(channelsPath) ? ReadCsv(channelsPath) : ReadCsv(legacyLinksPath);
    for (const auto& row : channelRows)
    {
        const std::string state = RequiredChannelState(row);
        data.channels.push_back({NormalizeChannelId(RequiredAny(row, "channel_id", "link_id")),
                                 Required(row, "src"),
                                 Required(row, "dst"),
                                 std::stod(Required(row, "bandwidth_mbps")),
                                 RequiredChannelCapacityMultiplier(row, state),
                                 state});
    }

    for (const auto& row : ReadCsv(JoinPath(sceneDirectory, "nics.csv")))
    {
        data.nics.push_back({Required(row, "nic_id"),
                             Required(row, "node"),
                             static_cast<uint32_t>(std::stoul(Required(row, "interface_index"))),
                             NormalizeChannelId(RequiredAny(row, "channel_id", "link_id")),
                             Required(row, "ip"),
                             Required(row, "mac"),
                             Required(row, "queue_policy"),
                             static_cast<uint32_t>(std::stoul(Required(row, "queue_size_packets"))),
                             RequiredNicState(row)});
    }

    std::ifstream routes(JoinPath(sceneDirectory, "routing_matrix.csv"));
    if (!routes)
    {
        throw std::runtime_error("Cannot open routing_matrix.csv");
    }
    std::string line;
    while (std::getline(routes, line))
    {
        if (Trim(line).empty())
        {
            continue;
        }
        std::vector<int> row;
        for (const auto& value : SplitCsvLine(line))
        {
            row.push_back(std::stoi(value));
        }
        data.routingMatrix.push_back(row);
    }

    std::ifstream traffic(JoinPath(sceneDirectory, "traffic.jsonl"));
    if (!traffic)
    {
        throw std::runtime_error("Cannot open traffic.jsonl");
    }
    while (std::getline(traffic, line))
    {
        if (Trim(line).empty())
        {
            continue;
        }
        auto featureModel = JsonStringValue(line, "feature_model");
        data.traffic.push_back({JsonStringValue(line, "flow_id"),
                                JsonStringValue(line, "src"),
                                JsonStringValue(line, "dst"),
                                JsonNumberValue(line, "demand_mbps"),
                                featureModel.empty() ? "cbr" : featureModel,
                                JsonNumberValue(line, "param_lambda"),
                                JsonNumberValue(line, "param_on_mean"),
                                JsonNumberValue(line, "param_off_mean"),
                                JsonNumberValue(line, "param_peak_rate_mbps")});
    }

    if (!eventFile.empty())
    {
        std::filesystem::path eventPath(eventFile);
        if (!eventPath.is_absolute())
        {
            eventPath = std::filesystem::path(sceneDirectory) / eventPath;
        }
        std::ifstream events(eventPath);
        if (!events)
        {
            throw std::runtime_error("Cannot open events jsonl: " + eventPath.string());
        }
        while (std::getline(events, line))
        {
            if (Trim(line).empty())
            {
                continue;
            }
            auto entityType = JsonStringValue(line, "entity_type");
            auto entityId = JsonStringValue(line, "entity_id");
            if (entityType == "link")
            {
                entityType = "channel";
                entityId = NormalizeChannelId(entityId);
            }
            data.events.push_back({JsonStringValue(line, "event_id"),
                                   JsonNumberValue(line, "time"),
                                   entityType,
                                   entityId,
                                   JsonStringValue(line, "event_type"),
                                   JsonNumberValue(line, "rate_multiplier", 1.0)});
        }
    }

    std::ifstream metadata(JoinPath(sceneDirectory, "metadata.json"));
    if (metadata)
    {
        std::ostringstream buffer;
        buffer << metadata.rdbuf();
        data.sceneDurationSeconds = JsonNumberValue(buffer.str(), "scene_duration", 300.0);
    }

    return data;
}

} // namespace ns3
