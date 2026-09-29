#include "network-scene-reader.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>
#include <unordered_map>

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
    if (state != "normal" && state != "disabled")
    {
        throw std::runtime_error("Unsupported node state: " + state +
                                 ". Expected normal or disabled");
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

std::vector<std::string>
JsonStringArrayValue(const std::string& line, const std::string& key)
{
    const std::string marker = "\"" + key + "\"";
    auto pos = line.find(marker);
    if (pos == std::string::npos)
    {
        return {};
    }
    pos = line.find('[', pos);
    if (pos == std::string::npos)
    {
        return {};
    }
    auto end = line.find(']', pos);
    if (end == std::string::npos)
    {
        return {};
    }

    std::vector<std::string> values;
    while (pos < end)
    {
        pos = line.find('"', pos + 1);
        if (pos == std::string::npos || pos >= end)
        {
            break;
        }
        auto valueEnd = line.find('"', pos + 1);
        if (valueEnd == std::string::npos || valueEnd > end)
        {
            break;
        }
        values.push_back(line.substr(pos + 1, valueEnd - pos - 1));
        pos = valueEnd;
    }
    return values;
}

std::vector<std::string>
ReadJsonLines(const std::string& path)
{
    std::ifstream input(path);
    if (!input)
    {
        throw std::runtime_error("Cannot open " + path);
    }
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(input, line))
    {
        if (!Trim(line).empty())
        {
            lines.push_back(line);
        }
    }
    return lines;
}

void
ValidateState(const std::string& state,
              const std::vector<std::string>& allowed,
              const std::string& entityType)
{
    if (std::find(allowed.begin(), allowed.end(), state) == allowed.end())
    {
        throw std::runtime_error("Unsupported " + entityType + " state: " + state);
    }
}

/** @brief Read an optional string field without masking an explicitly empty value.
 * @param line JSON record.
 * @param key Field name.
 * @param fallback Value for legacy records without the field.
 * @return Field value or the fallback.
 */
std::string
JsonNicString(const std::string& line, const std::string& key, const std::string& fallback)
{
    return line.find("\"" + key + "\"") == std::string::npos ? fallback : JsonStringValue(line, key);
}

/** @brief Validate a packet capacity before integer conversion.
 * @param value Requested capacity.
 * @return Valid capacity in packets.
 */
uint32_t
NicQueueSize(double value)
{
    if (!(value >= 1 && value <= 1000000) || value != static_cast<uint32_t>(value))
    {
        throw std::runtime_error("queue_size_packets must be an integer from 1 to 1000000");
    }
    return static_cast<uint32_t>(value);
}

/**
 * Read an optional Wi-Fi queue capacity, rejecting invalid explicit values.
 * @param line JSON record.
 * @return Capacity, or 256 packets for legacy records without this field.
 */
uint32_t
JsonWifiQueueSize(const std::string& line)
{
    if (line.find("\"queue_size_packets\"") == std::string::npos)
    {
        return 256;
    }
    return NicQueueSize(JsonNumberValue(line, "queue_size_packets", -1));
}

/**
 * Read merged JSONL scene files when present.
 * @param sceneDirectory Directory containing the scene files.
 * @param data Scene records to populate.
 * @return True if the JSONL scene exists, otherwise false.
 */
bool
ReadUnifiedSceneData(const std::string& sceneDirectory, NetworkSceneData& data)
{
    const auto nodesPath = JoinPath(sceneDirectory, "nodes.jsonl");
    if (!std::filesystem::exists(nodesPath))
    {
        return false;
    }

    for (const auto& line : ReadJsonLines(nodesPath))
    {
        const std::string id = JsonStringValue(line, "node_id");
        const std::string state = JsonStringValue(line, "state");
        ValidateState(state, {"normal", "disabled"}, "node");
        data.nodes.push_back({id, JsonStringValue(line, "role"), state});
        // Wired-only nodes need not have position or mobility records.
        if (line.find("\"mobility_model\"") != std::string::npos)
        {
            data.positions.push_back({id,
                                      JsonNumberValue(line, "x_m"),
                                      JsonNumberValue(line, "y_m"),
                                      JsonNumberValue(line, "z_m"),
                                      JsonStringValue(line, "mobility_model"),
                                      JsonNumberValue(line, "velocity_x_mps"),
                                      JsonNumberValue(line, "velocity_y_mps"),
                                      JsonNumberValue(line, "velocity_z_mps")});
        }
    }

    std::unordered_map<std::string, NetworkSceneWifiInterfaceRow> wifiInterfacesById;
    for (const auto& line : ReadJsonLines(JoinPath(sceneDirectory, "nics.jsonl")))
    {
        const std::string state = JsonStringValue(line, "state");
        ValidateState(state, {"normal", "disabled"}, "NIC");
        const std::string interfaceType = JsonStringValue(line, "interface_type");
        if (interfaceType == "wifi")
        {
            NetworkSceneWifiInterfaceRow iface = {
                JsonStringValue(line, "nic_id"),
                JsonStringValue(line, "node"),
                static_cast<uint32_t>(JsonNumberValue(line, "interface_index")),
                JsonStringValue(line, "channel_id"),
                JsonStringValue(line, "wifi_role"),
                JsonStringValue(line, "ip"),
                JsonStringValue(line, "mac"),
                state,
                JsonNicString(line, "queue_policy", "FIFO"),
                JsonWifiQueueSize(line),
                JsonNicString(line, "device_type", "wifi"),
                JsonNicString(line, "queue_layer", "traffic_control")};
            data.wifiInterfaces.push_back(iface);
            wifiInterfacesById[iface.id] = iface;
            data.networkMode = "hybrid";
        }
        else if (interfaceType == "wired")
        {
            data.nics.push_back({JsonStringValue(line, "nic_id"),
                                 JsonStringValue(line, "node"),
                                 static_cast<uint32_t>(
                                     JsonNumberValue(line, "interface_index")),
                                 NormalizeChannelId(JsonStringValue(line, "channel_id")),
                                 JsonStringValue(line, "ip"),
                                 JsonStringValue(line, "mac"),
                                 JsonStringValue(line, "queue_policy"),
                                 NicQueueSize(JsonNumberValue(line, "queue_size_packets")),
                                 state,
                                 JsonNicString(line, "device_type", "point_to_point"),
                                 JsonNicString(line, "queue_layer", "traffic_control")});
        }
        else
        {
            throw std::runtime_error("Unsupported interface_type: " + interfaceType);
        }
    }

    uint32_t associationSerial = 1;
    for (const auto& line : ReadJsonLines(JoinPath(sceneDirectory, "channels.jsonl")))
    {
        const std::string state = JsonStringValue(line, "state");
        const std::string mediumType = JsonStringValue(line, "medium_type");
        const std::string channelId = JsonStringValue(line, "channel_id");
        if (mediumType == "wired")
        {
            ValidateState(state, {"normal", "disabled", "degraded"}, "channel");
            const double capacityMultiplier =
                JsonNumberValue(line, "capacity_multiplier", 1.0);
            data.channels.push_back({NormalizeChannelId(channelId),
                                     JsonStringValue(line, "src"),
                                     JsonStringValue(line, "dst"),
                                     JsonNumberValue(line, "bandwidth_mbps"),
                                     capacityMultiplier,
                                     state});
        }
        else if (mediumType == "wifi")
        {
            ValidateState(state, {"normal", "disabled"}, "Wi-Fi channel");
            const std::string apNicId = JsonStringValue(line, "ap_nic_id");
            auto apIt = wifiInterfacesById.find(apNicId);
            if (apIt == wifiInterfacesById.end())
            {
                throw std::runtime_error("Unknown Wi-Fi AP NIC: " + apNicId);
            }
            data.wifiBss.push_back({channelId,
                                    apIt->second.node,
                                    JsonStringValue(line, "standard"),
                                    static_cast<uint16_t>(
                                        JsonNumberValue(line, "channel_number")),
                                    static_cast<uint16_t>(
                                        JsonNumberValue(line, "channel_width_mhz", 20)),
                                    JsonNumberValue(line, "tx_power_dbm", 18.0),
                                    JsonNumberValue(line, "loss_exponent", 3.0),
                                    JsonStringValue(line, "rate_manager"),
                                    state});
            for (const auto& staNicId : JsonStringArrayValue(line, "sta_nic_ids"))
            {
                auto staIt = wifiInterfacesById.find(staNicId);
                if (staIt == wifiInterfacesById.end())
                {
                    throw std::runtime_error("Unknown Wi-Fi STA NIC: " + staNicId);
                }
                std::ostringstream associationId;
                associationId << "A" << std::setw(4) << std::setfill('0')
                              << associationSerial++;
                data.wifiAssociations.push_back({associationId.str(),
                                                 channelId,
                                                 apIt->second.node,
                                                 staIt->second.node,
                                                 apNicId,
                                                 staNicId,
                                                 "enabled"});
            }
        }
        else
        {
            throw std::runtime_error("Unsupported medium_type: " + mediumType);
        }
    }

    const std::size_t nodeCount = data.nodes.size();
    data.routingMatrix.assign(nodeCount, std::vector<int>(nodeCount, -1));
    data.nextHopMatrix.assign(nodeCount, std::vector<int>(nodeCount, -1));
    std::unordered_map<std::string, std::size_t> nodeIndexById;
    for (std::size_t i = 0; i < nodeCount; ++i)
    {
        nodeIndexById[data.nodes[i].id] = i;
        data.routingMatrix[i][i] = 0;
        data.nextHopMatrix[i][i] = static_cast<int>(i);
    }
    for (const auto& line : ReadJsonLines(JoinPath(sceneDirectory, "routes.jsonl")))
    {
        const auto srcIt = nodeIndexById.find(JsonStringValue(line, "src"));
        const auto dstIt = nodeIndexById.find(JsonStringValue(line, "dst"));
        const auto nextIt = nodeIndexById.find(JsonStringValue(line, "next_hop"));
        if (srcIt == nodeIndexById.end() || dstIt == nodeIndexById.end() ||
            nextIt == nodeIndexById.end())
        {
            throw std::runtime_error("Route references an unknown node");
        }
        data.routingMatrix[srcIt->second][dstIt->second] =
            static_cast<int>(JsonNumberValue(line, "egress_interface", -1));
        data.nextHopMatrix[srcIt->second][dstIt->second] =
            static_cast<int>(nextIt->second);
    }
    return true;
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

    const bool unifiedScene = ReadUnifiedSceneData(sceneDirectory, data);
    if (!unifiedScene)
    {
        for (const auto& row : ReadCsv(JoinPath(sceneDirectory, "nodes.csv")))
        {
            data.nodes.push_back(
                {Required(row, "node_id"), Optional(row, "role"), RequiredNodeState(row)});
        }

        const auto channelsPath = JoinPath(sceneDirectory, "channels.csv");
        const auto legacyLinksPath = JoinPath(sceneDirectory, "links.csv");
        const auto channelRows = std::filesystem::exists(channelsPath)
                                     ? ReadCsv(channelsPath)
                                     : ReadCsv(legacyLinksPath);
        for (const auto& row : channelRows)
        {
            const std::string state = RequiredChannelState(row);
            data.channels.push_back(
                {NormalizeChannelId(RequiredAny(row, "channel_id", "link_id")),
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
                             NicQueueSize(std::stod(Required(row, "queue_size_packets"))),
                                 RequiredNicState(row),
                                 Optional(row, "device_type", "point_to_point"),
                                 Optional(row, "queue_layer", "traffic_control")});
        }

        const auto wifiBssPath = JoinPath(sceneDirectory, "wifi_bss.csv");
        if (std::filesystem::exists(wifiBssPath))
        {
            data.networkMode = "hybrid";
            for (const auto& row : ReadCsv(wifiBssPath))
            {
                data.wifiBss.push_back({Required(row, "bss_id"),
                                    Required(row, "ap_node"),
                                    Optional(row, "standard", "802.11g"),
                                    static_cast<uint16_t>(
                                        std::stoul(Optional(row, "channel_number", "1"))),
                                    static_cast<uint16_t>(
                                        std::stoul(Optional(row, "channel_width_mhz", "20"))),
                                    std::stod(Optional(row, "tx_power_dbm", "18.0")),
                                    std::stod(Optional(row, "loss_exponent", "3.0")),
                                    Optional(row, "rate_manager", "IdealWifiManager"),
                                        Optional(row, "state", "normal")});
            }
        }

        const auto wifiInterfacesPath = JoinPath(sceneDirectory, "wifi_interfaces.csv");
        if (std::filesystem::exists(wifiInterfacesPath))
        {
            for (const auto& row : ReadCsv(wifiInterfacesPath))
            {
                data.wifiInterfaces.push_back(
                    {Required(row, "nic_id"),
                 Required(row, "node"),
                 static_cast<uint32_t>(std::stoul(Required(row, "interface_index"))),
                 Required(row, "bss_id"),
                 Required(row, "wifi_role"),
                 Required(row, "ip"),
                 Required(row, "mac"),
                     Optional(row, "state", "normal"),
                     Optional(row, "queue_policy", "FIFO"),
                     NicQueueSize(std::stod(Optional(row, "queue_size_packets", "256"))),
                     Optional(row, "device_type", "wifi"),
                     Optional(row, "queue_layer", "traffic_control")});
            }
        }

        const auto wifiAssociationsPath = JoinPath(sceneDirectory, "wifi_associations.csv");
        if (std::filesystem::exists(wifiAssociationsPath))
        {
            for (const auto& row : ReadCsv(wifiAssociationsPath))
            {
                data.wifiAssociations.push_back({Required(row, "association_id"),
                                             Required(row, "bss_id"),
                                             Required(row, "ap_node"),
                                             Required(row, "sta_node"),
                                             Required(row, "ap_nic_id"),
                                             Required(row, "sta_nic_id"),
                                                 Optional(row, "configured_state", "enabled")});
            }
        }

        const auto positionsPath = JoinPath(sceneDirectory, "positions.csv");
        if (std::filesystem::exists(positionsPath))
        {
            for (const auto& row : ReadCsv(positionsPath))
            {
                data.positions.push_back({Required(row, "node_id"),
                                      std::stod(Required(row, "x_m")),
                                      std::stod(Required(row, "y_m")),
                                      std::stod(Required(row, "z_m")),
                                      Optional(row, "mobility_model", "constant"),
                                      std::stod(Optional(row, "velocity_x_mps", "0.0")),
                                      std::stod(Optional(row, "velocity_y_mps", "0.0")),
                                          std::stod(Optional(row, "velocity_z_mps", "0.0"))});
            }
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

        const auto nextHopMatrixPath = JoinPath(sceneDirectory, "next_hop_matrix.csv");
        if (std::filesystem::exists(nextHopMatrixPath))
        {
            std::ifstream nextHops(nextHopMatrixPath);
            while (std::getline(nextHops, line))
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
                data.nextHopMatrix.push_back(row);
            }
        }
    }

    std::string line;

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

    auto validateNic = [](const auto& nic, const std::string& expectedDevice) {
        if (nic.deviceType != expectedDevice)
        {
            throw std::runtime_error("Unsupported device_type for NIC " + nic.id + ": " + nic.deviceType);
        }
        if (nic.queueLayer != "traffic_control")
        {
            throw std::runtime_error("Unsupported queue_layer for NIC " + nic.id + ": " + nic.queueLayer);
        }
        ValidateState(nic.queuePolicy, {"FIFO", "RED", "CoDel", "FqCoDel"}, "queue policy");
        NicQueueSize(nic.queueSizePackets);
    };
    for (const auto& nic : data.nics)
    {
        validateNic(nic, "point_to_point");
    }
    for (const auto& nic : data.wifiInterfaces)
    {
        validateNic(nic, "wifi");
    }
    return data;
}

} // namespace ns3
