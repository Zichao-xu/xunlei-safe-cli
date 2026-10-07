#import <Foundation/Foundation.h>
#import <sys/stat.h>
#import <signal.h>

// Reply argument signedness is part of the XPC wire protocol (Thunder 5.80.0).
@protocol DownloadHostAgent
- (void)onFileNameChange:(NSDictionary *)value;
- (void)onTaskStateChange:(NSDictionary *)value;
- (void)onDatabaseErrorOccurred:(NSDictionary *)value;
- (void)onSubTaskStateChange:(NSDictionary *)value;
@end
@interface Host : NSObject<DownloadHostAgent>
@end
@implementation Host
- (void)onFileNameChange:(NSDictionary *)v {}
- (void)onTaskStateChange:(NSDictionary *)v {}
- (void)onDatabaseErrorOccurred:(NSDictionary *)v {}
- (void)onSubTaskStateChange:(NSDictionary *)v {}
@end
@protocol DownloadServiceProtocol
- (void)getVersionwithReply:(void (^)(NSString *))reply;
- (void)initETM:(NSDictionary *)context withReply:(void (^)(int))reply;
- (void)createTask:(NSDictionary *)task withReply:(void (^)(int, int))reply;
- (void)getTaskInfo:(unsigned int)taskId withReply:(void (^)(NSDictionary *))reply;
- (void)getTorrentInfo:(NSString *)path withReply:(void (^)(NSData *))reply;
- (void)uninitETM;
@end
static BOOL connectionFailed = NO;
static volatile sig_atomic_t cancelled = 0;
static void cancel(int sig) { cancelled = 1; }
static BOOL waitFor(BOOL *done) {
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:15];
    while (!*done && !connectionFailed && !cancelled && deadline.timeIntervalSinceNow > 0)
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
    return *done && !connectionFailed && !cancelled;
}
static void save(NSMutableDictionary *job, NSString *path) {
    job[@"updatedAt"] = @([NSDate date].timeIntervalSince1970);
    NSData *data = [NSJSONSerialization dataWithJSONObject:job options:0 error:NULL];
    [data writeToFile:path options:NSDataWritingAtomic error:NULL];
    chmod(path.fileSystemRepresentation, 0600);
}
static BOOL terminal(NSDictionary *task) {
    return [@[@"已完成", @"失败"] containsObject:task[@"status"]];
}
int main(int argc, const char *argv[]) { @autoreleasepool {
    umask(0077);
    signal(SIGTERM, cancel);
    signal(SIGINT, cancel);
    if (argc != 2) return 64;
    NSString *path = [NSString stringWithUTF8String:argv[1]];
    NSMutableDictionary *job = [NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:path]
        options:NSJSONReadingMutableContainers error:NULL];
    if (!job) return 65;
    job[@"pid"] = @([[NSProcessInfo processInfo] processIdentifier]);
    NSArray *tasks = job[@"tasks"];
    NSXPCConnection *connection = [[NSXPCConnection alloc] initWithServiceName:@"com.xunlei.DownloadService"];
    connection.remoteObjectInterface = [NSXPCInterface interfaceWithProtocol:@protocol(DownloadServiceProtocol)];
    connection.exportedInterface = [NSXPCInterface interfaceWithProtocol:@protocol(DownloadHostAgent)];
    connection.exportedObject = [Host new];
    connection.interruptionHandler = ^{ connectionFailed = YES; };
    [connection resume];
    id<DownloadServiceProtocol> service = [connection remoteObjectProxyWithErrorHandler:^(NSError *e) {
        connectionFailed = YES;
    }];
    NSString *failure = @"迅雷后台服务连接失败或超时";
    __block BOOL done = NO;
    __block NSString *version;
    __block int result = -1;
    [service getVersionwithReply:^(NSString *v) { version = v; done = YES; }];
    BOOL initialized = NO;
    if (!waitFor(&done) || ![version isEqualToString:@"11.0114.460.24 - 11.0114.24r"]) {
        failure = @"迅雷内核版本不兼容，请检查 xl 与迅雷版本";
        goto finish;
    }
    job[@"kernel"] = version;
    done = NO;
    [service initETM:job[@"context"] withReply:^(int v) { result = v; done = YES; }];
    if (!waitFor(&done) || result != 0) {
        failure = @"迅雷内核初始化失败";
        goto finish;
    }
    initialized = YES;
    for (NSMutableDictionary *task in tasks) {
        done = NO;
        __block int ident = 0;
        result = -1;
        [service createTask:task[@"request"] withReply:^(int v, int tid) {
            result = v; ident = tid; done = YES;
        }];
        if (!waitFor(&done)) goto finish;
        if (result != 0 || ident <= 0) {
            task[@"status"] = @"失败";
            task[@"message"] = @"迅雷无法创建此下载，请检查链接或种子";
            task[@"errorCode"] = @(result);
        } else {
            // The official service starts the task in createTask; no UI confirmation.
            task[@"nativeId"] = @(ident);
            task[@"status"] = [task[@"request"][@"taskType"] intValue] == 4 ? @"获取种子" : @"下载中";
        }
        save(job, path);
    }
    job[@"ready"] = @YES;
    save(job, path);
    while (!connectionFailed && !cancelled) { @autoreleasepool {
        BOOL pending = NO;
        for (NSMutableDictionary *task in tasks) {
            if (terminal(task)) continue;
            pending = YES;
            done = NO;
            __block NSDictionary *info;
            [service getTaskInfo:[task[@"nativeId"] unsignedIntValue] withReply:^(NSDictionary *v) {
                info = v; done = YES;
            }];
            if (!waitFor(&done) || !info.count) goto finish;
            task[@"info"] = info;
            NSString *returnedName = info[@"taskName"];
            if (returnedName.length && ![returnedName isEqualToString:@"(null)"])
                task[@"name"] = returnedName;
            long long size = [info[@"fileSize"] longLongValue];
            long long bytes = [info[@"downloadedSize"] longLongValue];
            task[@"progress"] = size > 0 ? @(MIN(1.0, (double)bytes / size)) : @0;
            int state = [info[@"state"] intValue];
            if (state == 3 && [info[@"type"] intValue] == 4) {
                // Magnet tasks first fetch metadata. Completion must mean payload completion.
                NSString *seed = [info[@"taskDir"] stringByAppendingPathComponent:info[@"taskName"]];
                done = NO;
                __block NSData *metadata;
                [service getTorrentInfo:seed withReply:^(NSData *v) { metadata = v; done = YES; }];
                if (!waitFor(&done)) goto finish;
                id torrent = metadata ? [NSJSONSerialization JSONObjectWithData:metadata options:0 error:NULL] : nil;
                NSString *name = [torrent isKindOfClass:NSDictionary.class] ? torrent[@"baseFolderName"] : nil;
                if (![name isKindOfClass:NSString.class] || !name.length || [name containsString:@"/"] || [name containsString:@"\\"]
                    || [name isEqualToString:@".."] || [name isEqualToString:@"."]) {
                    task[@"status"] = @"失败"; task[@"message"] = @"磁力种子解析失败";
                    continue;
                }
                NSMutableDictionary *request = [task[@"request"] mutableCopy];
                [request removeObjectForKey:@"url"];
                request[@"taskType"] = @1;
                request[@"seedPath"] = seed;
                request[@"fileName"] = name;
                done = NO;
                __block int newId = 0;
                result = -1;
                [service createTask:request withReply:^(int v, int tid) { result = v; newId = tid; done = YES; }];
                if (!waitFor(&done)) goto finish;
                if (result != 0 || newId <= 0) {
                    task[@"status"] = @"失败"; task[@"message"] = @"磁力种子已取得，但迅雷无法创建文件下载";
                } else {
                    task[@"nativeId"] = @(newId); task[@"name"] = name;
                    task[@"progress"] = @0; task[@"status"] = @"下载中";
                }
            } else if (state == 3) {
                NSString *target = [info[@"taskDir"] stringByAppendingPathComponent:task[@"name"]];
                if (size >= 0 && bytes >= size && [info[@"finishedTime"] longLongValue] > 0
                    && [[NSFileManager defaultManager] fileExistsAtPath:target]) {
                    task[@"status"] = @"已完成";
                    task[@"progress"] = @1;
                } else { task[@"status"] = @"失败"; task[@"message"] = @"下载结果不完整"; }
            } else if (state == 4) {
                task[@"status"] = @"失败";
                task[@"message"] = @"迅雷下载失败，请检查来源或网络";
                task[@"errorCode"] = info[@"failedCode"] ?: @0;
            }
        }
        save(job, path);
        if (!pending) { failure = nil; break; }
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.5]];
    } }
finish:
    if (cancelled) failure = @"后台下载已停止";
    if (failure) {
        for (NSMutableDictionary *task in tasks) if (!terminal(task)) {
            task[@"status"] = @"失败"; task[@"message"] = failure;
        }
    }
    job[@"ready"] = @YES;
    job[@"stopped"] = @YES;
    save(job, path);
    if (initialized && !connectionFailed) {
        [service uninitETM];
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.2]];
    }
    [connection invalidate];
    return failure ? 1 : 0;
} }
