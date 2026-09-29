#ifndef NETWORK_SCENE_READER_H
#define NETWORK_SCENE_READER_H

#include "network-scene-traffic.h"

#include <cstdint>
#include <string>
#include <vector>

namespace ns3
{

struct NetworkSceneNodeRow
{
    std::string id;
    std::string role;
    std::string state;
};

struct NetworkSceneChannelRow
{
    std::string id;
    std::string src;
    std::string dst;
    double bandwidthMbps{0.0};
    double capacityMultiplier{1.0};
    std::string state;
};

struct NetworkSceneNicRow
{
    std::string id;
    std::string node;
    uint32_t interfaceIndex{0};
    std::string channelId;
    std::string ipCidr;
    std::string mac;
    std::string queuePolicy;
    uint32_t queueSizePackets{0};
    std::string state;
    std::string deviceType{"point_to_point"}; ///< Concrete simulated device model.
    std::string queueLayer{"traffic_control"}; ///< Layer represented by queue fields.
};

struct NetworkSceneWifiBssRow
{
    std::string id; ///< Wireless channel ID, also used as the runtime SSID.
    std::string apNode;
    std::string standard;
    uint16_t channelNumber{0};
    uint16_t channelWidthMhz{20};
    double txPowerDbm{18.0};
    double lossExponent{3.0};
    std::string rateManager{"IdealWifiManager"};
    std::string state;
};

struct NetworkSceneWifiInterfaceRow
{
    std::string id;
    std::string node;
    uint32_t interfaceIndex{0};
    std::string bssId;
    std::string wifiRole;
    std::string ipCidr;
    std::string mac;
    std::string state;
    std::string queuePolicy{"FIFO"}; ///< Common traffic-control policy; legacy default.
    uint32_t queueSizePackets{256}; ///< Nominal queue capacity; legacy default.
    std::string deviceType{"wifi"}; ///< Concrete simulated device model.
    std::string queueLayer{"traffic_control"}; ///< Does not describe the WiFi MAC queue.
};

struct NetworkSceneWifiAssociationRow
{
    std::string id;
    std::string bssId;
    std::string apNode;
    std::string staNode;
    std::string apNicId;
    std::string staNicId;
    std::string configuredState;
};

struct NetworkScenePositionRow
{
    std::string node;
    double x{0.0};
    double y{0.0};
    double z{0.0};
    std::string mobilityModel;
    double velocityX{0.0};
    double velocityY{0.0};
    double velocityZ{0.0};
};

struct NetworkSceneEventRow
{
    std::string id;
    double timeSeconds{0.0};
    std::string entityType;
    std::string entityId;
    std::string eventType;
    double rateMultiplier{1.0};
};

struct NetworkSceneData
{
    std::string networkMode{"wired"};
    std::vector<NetworkSceneNodeRow> nodes;
    std::vector<NetworkSceneChannelRow> channels;
    std::vector<NetworkSceneNicRow> nics;
    std::vector<NetworkSceneWifiBssRow> wifiBss;
    std::vector<NetworkSceneWifiInterfaceRow> wifiInterfaces;
    std::vector<NetworkSceneWifiAssociationRow> wifiAssociations;
    std::vector<NetworkScenePositionRow> positions;
    std::vector<std::vector<int>> routingMatrix;
    std::vector<std::vector<int>> nextHopMatrix;
    std::vector<NetworkSceneTrafficPattern> traffic;
    std::vector<NetworkSceneEventRow> events;
    double sceneDurationSeconds{300.0};
};

std::string ResolveNetworkSceneDirectory(const std::string& value);
std::string NetworkSceneBaseName(const std::string& path);
NetworkSceneData ReadNetworkSceneData(const std::string& sceneDirectory, const std::string& eventFile = "");

} // namespace ns3

#endif /* NETWORK_SCENE_READER_H */
